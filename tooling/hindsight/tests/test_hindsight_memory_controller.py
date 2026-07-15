import copy
import hashlib
from dataclasses import replace
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import runpy
import signal
import stat
import subprocess
import sys
import tempfile
from types import MappingProxyType
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
TRUSTED_TEST_PYTHON = "/usr/bin/python3"
CLI = ROOT / "bin/hindsight-memory"
LIB = ROOT / "lib"
if str(LIB) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(LIB))

from hindsight_memory_control_plane.canonical import (
    StrictJsonError,
    canonical_bytes,
    digest,
    strict_json_loads,
)
from hindsight_memory_control_plane.adapters import FakeAdapter
from hindsight_memory_control_plane.inventory import (
    InventoryError,
    _resolved_artifact,
    load_inventory,
)
from hindsight_memory_control_plane.model import OperationSnapshot
from hindsight_memory_control_plane.planning import (
    PlanError,
    build_plan,
    inventory_endpoint,
    plan_from_dict,
    verify_plan,
)
from hindsight_memory_control_plane.reconcile import (
    build_mutation_plan,
    capture_migration_gate,
)
import hindsight_memory_control_plane.ledger as ledger_module
from hindsight_memory_control_plane.ledger import (
    MAX_LEDGER_RECORD_BYTES,
    TAIL_VALIDATION_BYTES,
    LedgerError,
    append_record,
    append_record_once,
    validate_record,
)


def interpreter_prefix_probe(argv):
    runtime_file = Path(__file__).resolve()
    values = (
        runtime_file.parent,
        runtime_file.parent,
        runtime_file.parent,
        runtime_file.parent,
        runtime_file,
    )
    return subprocess.CompletedProcess(
        argv,
        0,
        "\n".join(
            str(value).encode("utf-8").hex() for value in values
        ) + "\n",
        "",
    )


def inventory():
    return {
        "schema_version": 1,
        "machine": {"id": "test-mac", "base_port": 7979},
        "archetype": {"id": "trusted-workstation"},
        "profiles": [
            {
                "id": "core",
                "slot": 0,
                "enabled": True,
                "host": "127.0.0.1",
                "roles": {
                    "llm": "local-llm",
                    "embedding": "local-embedding",
                    "reranking": "local-reranker",
                },
                "data_classes": ["engineering", "personal"],
            }
        ],
        "providers": [
            {
                "id": "local-llm",
                "role": "llm",
                "placement": "local",
                "data_classes": ["engineering", "personal"],
            },
            {
                "id": "local-embedding",
                "role": "embedding",
                "placement": "local",
                "data_classes": ["engineering", "personal"],
            },
            {
                "id": "local-reranker",
                "role": "reranking",
                "placement": "local",
                "data_classes": ["engineering", "personal"],
            },
        ],
        "banks": [
            {
                "id": "engineering",
                "profile_id": "core",
                "data_class": "engineering",
                "authority": "authoritative",
                "writable": True,
            }
        ],
        "harnesses": [
            {
                "id": "codex",
                "profile_id": "core",
                "home_bank": {"profile_id": "core", "bank_id": "engineering"},
                "write_bank": {"profile_id": "core", "bank_id": "engineering"},
            }
        ],
        "migration": {
            "artifact_dir": "/tmp/hindsight-artifacts",
            "proposal_log": "/tmp/hindsight-proposals.md",
        },
        "policy": {
            "engineering_memory_enabled": True,
            "allowed_placements": {
                "engineering": ["local", "private-remote"],
                "personal": ["local", "private-remote"],
            },
        },
    }


class ControllerCliTest(unittest.TestCase):
    def test_build_plan_accepts_read_only_mapping_live_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            self.write_json(path, inventory())
            desired = load_inventory(path)
        live = MappingProxyType(
            {
                "profile_id": "core",
                "endpoint": MappingProxyType(
                    {
                        "profile_id": "core",
                        "scheme": "http",
                        "host": "127.0.0.1",
                        "port": 7979,
                        "tenant": "default",
                    }
                ),
                "compatibility": [],
                "state": MappingProxyType({"banks": []}),
            }
        )
        plan = build_plan(
            desired,
            live,
            MappingProxyType({"idle": True, "active": []}),
        )
        self.assertEqual(plan.target_profile, "core")

    def test_inventory_identifiers_and_endpoint_host_match_planning_contract(self):
        mutations = (
            lambda value: value["profiles"][0].update({"id": "bad profile"}),
            lambda value: value["banks"][0].update({"id": "bad bank"}),
            lambda value: value["profiles"][0].update({"tenant": "bad tenant"}),
            lambda value: value["profiles"][0].update({"host": "x" * 254}),
            lambda value: value["banks"][0].update(
                {"models": [{"id": "bad model", "revision": "rev-1"}]}
            ),
            lambda value: value["banks"][0].update(
                {"models": [{"id": "model-1", "revision": "bad revision"}]}
            ),
            lambda value: value["banks"][0].update(
                {"directives": [{"id": "bad directive", "text": "safe"}]}
            ),
            lambda value: value["banks"][0].update(
                {"directives": [{"id": "directive-1", "text": " \t\n"}]}
            ),
            lambda value: value["banks"][0].update(
                {
                    "models": [
                        {"id": "model-1", "revision": "rev-1"},
                        {"id": "model-1", "revision": "rev-2"},
                    ]
                }
            ),
            lambda value: value["banks"][0].update(
                {
                    "directives": [
                        {"id": "directive-1", "text": "one"},
                        {"id": "directive-1", "text": "two"},
                    ]
                }
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            for mutate in mutations:
                value = inventory()
                mutate(value)
                self.write_json(path, value)
                with self.subTest(value=value), self.assertRaises(
                    InventoryError
                ):
                    load_inventory(path)

    def test_inventory_base_port_is_always_a_valid_tcp_port(self):
        for base_port in (0, -1, 65_536):
            with self.subTest(base_port=base_port), tempfile.TemporaryDirectory() as directory:
                value = inventory()
                value["machine"]["base_port"] = base_port
                path = Path(directory) / "inventory.json"
                self.write_json(path, value)
                with self.assertRaisesRegex(InventoryError, "base_port"):
                    load_inventory(path)

    def test_inventory_canonicalizes_host_before_http_loopback_check(self):
        value = inventory()
        value["profiles"][0]["host"] = "0:0:0:0:0:0:0:1"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            self.write_json(path, value)
            desired = load_inventory(path)
        self.assertEqual(inventory_endpoint(desired, "core").host, "::1")

    def test_inventory_rejects_harness_binding_to_disabled_profile(self):
        value = inventory()
        value["profiles"].append(
            {**value["profiles"][0], "id": "disabled", "slot": 1, "enabled": False}
        )
        value["banks"].append(
            {
                **value["banks"][0],
                "id": "disabled-engineering",
                "profile_id": "disabled",
            }
        )
        value["harnesses"][0].update(
            {
                "profile_id": "disabled",
                "home_bank": {
                    "profile_id": "disabled",
                    "bank_id": "disabled-engineering",
                },
                "write_bank": {
                    "profile_id": "disabled",
                    "bank_id": "disabled-engineering",
                },
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            self.write_json(path, value)
            with self.assertRaisesRegex(InventoryError, "disabled profile"):
                load_inventory(path)

    def test_plan_deserialization_and_verification_reject_remote_cleartext_http(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            self.write_json(path, inventory())
            desired = load_inventory(path)
        plan = build_plan(
            desired,
            {
                "profile_id": "core",
                "endpoint": {
                    "profile_id": "core",
                    "scheme": "http",
                    "host": "127.0.0.1",
                    "port": 7979,
                    "tenant": "default",
                },
                "state": {"banks": []},
                "compatibility": [],
            },
            {"idle": True, "active": []},
        )
        remote_endpoint = type(plan.target_endpoint)(
            profile_id="core",
            scheme="http",
            host="remote.invalid",
            port=7979,
            tenant="default",
        )
        serialized = plan.to_dict()
        serialized["target_endpoint"] = remote_endpoint.to_dict()
        with self.assertRaisesRegex(PlanError, "literal loopback"):
            plan_from_dict(serialized)
        with self.assertRaisesRegex(PlanError, "literal loopback"):
            verify_plan(replace(plan, target_endpoint=remote_endpoint))

    def test_plan_verification_rejects_noncanonical_endpoint_object(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            self.write_json(path, inventory())
            desired = load_inventory(path)
        plan = build_plan(
            desired,
            {
                "profile_id": "core",
                "endpoint": {
                    "profile_id": "core",
                    "scheme": "http",
                    "host": "127.0.0.1",
                    "port": 7979,
                    "tenant": "default",
                },
                "state": {"banks": []},
                "compatibility": [],
            },
            {"idle": True, "active": []},
        )
        noncanonical = replace(plan.target_endpoint, host="0:0:0:0:0:0:0:1")
        with self.assertRaisesRegex(PlanError, "endpoint is not canonical"):
            verify_plan(replace(plan, target_endpoint=noncanonical))

    def test_inventory_scopes_bank_ids_to_profiles_and_closes_placement_classes(self):
        value = inventory()
        value["profiles"].append(
            {
                **value["profiles"][0],
                "id": "secondary",
                "slot": 1,
                "data_classes": ["personal"],
            }
        )
        value["banks"].append(
            {
                "id": "engineering",
                "profile_id": "secondary",
                "data_class": "personal",
                "authority": "none",
                "writable": True,
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            self.write_json(path, value)
            desired = load_inventory(path)
            self.assertEqual(
                {
                    (bank["profile_id"], bank["id"])
                    for bank in desired.banks
                },
                {("core", "engineering"), ("secondary", "engineering")},
            )

            value["policy"]["allowed_placements"]["unknown"] = ["local"]
            self.write_json(path, value)
            with self.assertRaisesRegex(InventoryError, "unknown data classes"):
                load_inventory(path)

            for allowed in (["local", "typo-placement"], ["local", "local"], [7]):
                value = inventory()
                value["policy"]["allowed_placements"]["engineering"] = allowed
                self.write_json(path, value)
                with self.assertRaisesRegex(InventoryError, "unique supported placements"):
                    load_inventory(path)

    def test_missing_profile_artifact_digest_is_configuration_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            self.write_json(path, inventory())
            desired = load_inventory(path)
            plan = build_plan(
                desired,
                {
                    "profile_id": "core",
                    "endpoint": {
                        "profile_id": "core",
                        "scheme": "http",
                        "host": "127.0.0.1",
                        "port": 7979,
                        "tenant": "default",
                    },
                    "state": {"banks": []},
                    "compatibility": [],
                },
                {"idle": True, "active": []},
            )
            self.assertEqual(plan.actions[0].kind, "configure_profile")

    def test_inventory_serialization_separates_digest_body_and_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            self.write_json(path, inventory())
            desired = load_inventory(path)
            self.assertNotIn("inventory_digest", desired.body())
            self.assertNotIn("artifact_digest", desired.body())
            self.assertEqual(
                desired.to_dict()["inventory_digest"], desired.inventory_digest
            )
            self.assertEqual(
                desired.to_dict()["artifact_digest"], desired.artifact_digest
            )

    def test_plan_requires_an_explicit_object_live_state_surface(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            self.write_json(path, inventory())
            desired = load_inventory(path)
            base = {
                "profile_id": "core",
                "endpoint": {
                    "profile_id": "core", "scheme": "http",
                    "host": "127.0.0.1", "port": 7979,
                    "tenant": "default",
                },
                "compatibility": [],
            }
            malformed_states = (
                {},
                {"state": []},
                {"state": {}, "live_state": {"banks": []}},
            )
            for malformed in malformed_states:
                with (
                    self.subTest(malformed=malformed),
                    self.assertRaises(PlanError),
                ):
                    build_plan(
                        desired,
                        {**base, **malformed},
                        {"idle": True, "active": []},
                    )

    def test_compatibility_endpoint_must_equal_the_full_target_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            self.write_json(path, inventory())
            desired = load_inventory(path)
            endpoint = {
                "profile_id": "core",
                "scheme": "http",
                "host": "127.0.0.1",
                "port": 7979,
                "tenant": "default",
            }
            compatibility = {
                "check": "endpoint-contract",
                "compatible": True,
                "status": "pass",
                "endpoint": {**endpoint, "tenant": "other"},
            }
            with self.assertRaisesRegex(
                PlanError, "compatibility endpoint identity"
            ):
                build_plan(
                    desired,
                    {
                        "profile_id": "core",
                        "endpoint": endpoint,
                        "state": {"banks": []},
                        "compatibility": [compatibility],
                    },
                    {"idle": True, "active": []},
                )

    def test_inventory_nested_schemas_are_closed_and_typed(self):
        for mutate in (
            lambda value: value["profiles"][0].update({"private_token": "secret"}),
            lambda value: value["harnesses"][0]["home_bank"].update({"extra": True}),
            lambda value: value["banks"][0].update({"models": [{"id": "m", "revision": 1}]}),
        ):
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as directory:
                value = inventory()
                mutate(value)
                path = Path(directory) / "inventory.json"
                self.write_json(path, value)
                with self.assertRaises(InventoryError):
                    load_inventory(path)

    def test_inventory_rejects_duplicate_provider_ids_within_a_role(self):
        with tempfile.TemporaryDirectory() as directory:
            value = inventory()
            value["profiles"][0]["roles"]["llm"] = [
                "local-llm",
                "local-llm",
            ]
            path = Path(directory) / "inventory.json"
            self.write_json(path, value)
            with self.assertRaisesRegex(InventoryError, "provider IDs.*unique"):
                load_inventory(path)

    def test_ledger_timestamp_must_name_a_real_instant(self):
        value = {
            "schema_version": 1,
            "action_id": "retain-1",
            "correlation_id": "session-1",
            "source_bank": {"profile_id": "core", "bank_id": "engineering", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "target_bank": {"profile_id": "core", "bank_id": "personal", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "policy_digest": "1" * 64,
            "artifact_digest": "2" * 64,
            "decision": "deny",
            "reason_code": "POLICY_DENY",
            "timestamp": "2026-02-30T12:00:00Z",
            "reversible_record_id": None,
        }
        with self.assertRaisesRegex(LedgerError, "real UTC"):
            validate_record(value)

    def test_ledger_plain_http_endpoint_requires_literal_loopback(self):
        value = {
            "schema_version": 1,
            "action_id": "retain-loopback",
            "correlation_id": "session-loopback",
            "source_bank": {"profile_id": "core", "bank_id": "engineering", "endpoint": {"profile_id": "core", "scheme": "http", "host": "example.com", "port": 7979, "tenant": "default"}},
            "target_bank": {"profile_id": "core", "bank_id": "personal", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "policy_digest": "1" * 64,
            "artifact_digest": "2" * 64,
            "decision": "deny",
            "reason_code": "LOOPBACK_TEST",
            "timestamp": "2026-07-12T17:00:00Z",
            "reversible_record_id": None,
        }
        with self.assertRaisesRegex(LedgerError, "literal loopback"):
            validate_record(value)

        value["source_bank"]["endpoint"]["scheme"] = "https"
        validate_record(value)

        value["source_bank"]["endpoint"]["host"] = "Example.COM."
        with self.assertRaisesRegex(LedgerError, "canonical"):
            validate_record(value)
        value["source_bank"]["endpoint"]["host"] = "example.com"

        for field, replacement, message in (
            ("scheme", [], "scheme must be http or https"),
            ("decision", [], "decision is not a supported enum"),
        ):
            invalid = copy.deepcopy(value)
            if field == "scheme":
                invalid["source_bank"]["endpoint"][field] = replacement
            else:
                invalid[field] = replacement
            with self.subTest(field=field), self.assertRaisesRegex(
                LedgerError, message
            ):
                validate_record(invalid)

    def run_cli(self, state_dir, *args):
        return subprocess.run(
            [sys.executable, str(CLI), "--state-dir", str(state_dir), *map(str, args)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )

    def test_run_cli_uses_a_finite_timeout(self):
        with patch("subprocess.run") as run:
            self.run_cli("/tmp/state", "validate", "--inventory", "/tmp/inventory")
        self.assertEqual(run.call_args.kwargs["timeout"], 30)

    def write_json(self, path, value, *, pretty=False):
        path.write_text(
            json.dumps(value, indent=2 if pretty else None, ensure_ascii=False),
            encoding="utf-8",
        )
        path.chmod(0o600)

    def test_prd_validator_catches_validation_errors_from_self_tests(self):
        module = runpy.run_path(
            str(
                ROOT
                / "tests"
                / "hindsight_memory_control_plane_prd_validation.py"
            )
        )
        module["main"].__globals__["validate_synthetic_migration_cases"] = (
            lambda: module["reject"]("synthetic_self_test_failure")
        )
        stderr = StringIO()
        with patch.object(
            sys,
            "argv",
            ["validator", "catalog", "prd", "repo", "base"],
        ), redirect_stderr(stderr):
            self.assertEqual(module["main"](), 1)
        self.assertIn("synthetic_self_test_failure", stderr.getvalue())

    def test_prd_validator_does_not_treat_empty_bytes_as_forbidden(self):
        module = runpy.run_path(
            str(
                ROOT
                / "tests"
                / "hindsight_memory_control_plane_prd_validation.py"
            )
        )
        self.assertFalse(
            module["contains_forbidden"](b"", ("private-control-plane-marker",))
        )

    def test_prd_validator_decomposes_precomposed_accent_adversaries(self):
        module = runpy.run_path(
            str(
                ROOT
                / "tests"
                / "hindsight_memory_control_plane_prd_validation.py"
            )
        )
        self.assertTrue(
            module["contains_forbidden"](
                "prívate-control-plane-marker",
                ("private-control-plane-marker",),
            )
        )

    def test_package_root_imports_only_cycle_free_core_modules(self):
        script = (
            "import json,sys;"
            f"sys.path.insert(0,{str(LIB)!r});"
            "import hindsight_memory_control_plane as package;"
            "allowed={'hindsight_memory_control_plane.canonical',"
            "'hindsight_memory_control_plane.model'};"
            "print(json.dumps(sorted(name for name in sys.modules "
            "if name.startswith('hindsight_memory_control_plane.') "
            "and name not in allowed)))"
        )
        result = subprocess.run(
            [TRUSTED_TEST_PYTHON, "-c", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

    def test_canonical_json_rejects_non_finite_numbers(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonical_bytes({"value": value})

    def test_canonical_json_rejects_nested_non_string_object_keys(self):
        with self.assertRaisesRegex(ValueError, "object keys must be strings"):
            canonical_bytes({"outer": [{1: "ambiguous"}]})

    def test_canonical_json_uses_the_bounded_interoperable_number_profile(self):
        self.assertEqual(
            canonical_bytes(
                {
                    "fraction": 1.5,
                    "integer": 1.0,
                    "max_safe_integer": 9007199254740991,
                    "negative_zero": -0.0,
                }
            ),
            b'{"fraction":1.5,"integer":1,"max_safe_integer":9007199254740991,"negative_zero":0}',
        )
        for value in (
            9007199254740992,
            -9007199254740992,
            9007199254740992.0,
            -9007199254740992.0,
        ):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "safe integer",
            ):
                canonical_bytes({"value": value})

    def test_canonical_json_uses_rfc8785_key_and_number_serialization(self):
        self.assertEqual(
            canonical_bytes({"value": 1e-6, "tiny": 1e-7}),
            b'{"tiny":1e-7,"value":0.000001}',
        )
        self.assertEqual(
            canonical_bytes({"\U00010000": 1, "\ue000": 2}),
            '{"\U00010000":1,"\ue000":2}'.encode(),
        )

    def test_strict_json_rejects_duplicate_object_keys(self):
        with self.assertRaisesRegex(ValueError, "duplicate JSON object key: value"):
            strict_json_loads('{"value":1,"value":2}')

    def test_strict_json_rejects_lone_surrogates_in_keys_and_values(self):
        for encoded in ('{"value":"\\ud800"}', '{"\\udfff":"value"}'):
            with self.subTest(encoded=encoded), self.assertRaisesRegex(
                ValueError, "non-scalar Unicode",
            ):
                strict_json_loads(encoded)

    def test_canonical_json_rejects_non_scalars_and_unsupported_values(self):
        for value in ("\ud800", {"\udfff": "value"}):
            with self.subTest(value=value), self.assertRaisesRegex(
                StrictJsonError, "non-scalar Unicode"
            ):
                canonical_bytes(value)
        for value in ({"not", "json"}, b"bytes", object()):
            with self.subTest(value=type(value).__name__), self.assertRaisesRegex(
                StrictJsonError, "unsupported canonical JSON value type"
            ):
                canonical_bytes({"value": value})

    def test_strict_json_rejects_non_finite_constants(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, f"non-finite JSON constant: {value}",
            ):
                strict_json_loads(f'{{"value":{value}}}')

    def test_strict_json_rejects_floating_point_overflow(self):
        for value in ("1e309", "-1e309"):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, f"non-finite JSON number: {value}",
            ):
                strict_json_loads(f'{{"value":{value}}}')

    def test_strict_json_normalizes_integral_floats_and_rejects_unsafe_integers(self):
        self.assertEqual(strict_json_loads('{"value":1.0}'), {"value": 1})
        self.assertEqual(strict_json_loads('{"value":-0.0}'), {"value": 0})
        for value in ("9007199254740992", "-9007199254740992"):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "safe integer",
            ):
                strict_json_loads(f'{{"value":{value}}}')

    def test_strict_json_rejects_fractional_precision_loss_and_underflow(self):
        for value in (
            "1.0000000000000001",
            "1.23456789012345678",
            "1e-324",
            "-1e-324",
        ):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "loses precision",
            ):
                strict_json_loads(f'{{"value":{value}}}')
        self.assertEqual(
            strict_json_loads('{"value":0.10000000000000000}'),
            {"value": 0.1},
        )

    def test_cli_rejects_ambiguous_inventory_json_before_digesting(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            encoded = json.dumps(inventory())
            cases = {
                "duplicate": encoded.replace(
                    '"schema_version": 1',
                    '"schema_version": 1, "schema_version": 1',
                    1,
                ),
                "nan": encoded.replace('"base_port": 7979', '"base_port": NaN', 1),
                "infinity": encoded.replace(
                    '"base_port": 7979', '"base_port": Infinity', 1,
                ),
                "negative-infinity": encoded.replace(
                    '"base_port": 7979', '"base_port": -Infinity', 1,
                ),
            }
            for name, raw in cases.items():
                with self.subTest(name=name):
                    fixture = tmp / f"{name}.json"
                    fixture.write_text(raw, encoding="utf-8")
                    result = self.run_cli(tmp, "validate", "--inventory", fixture)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("cannot load inventory", result.stderr)

    def test_rollback_archive_overlap_detects_path_and_inode_aliases(self):
        module = runpy.run_path(str(CLI))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "incoming.zip"
            incoming.write_bytes(b"archive")
            hardlink = root / "hardlink.zip"
            os.link(incoming, hardlink)
            self.assertTrue(module["_paths_overlap"](incoming, [incoming]))
            self.assertTrue(module["_paths_overlap"](hardlink, [incoming]))
            self.assertFalse(module["_paths_overlap"](root / "rollback.tar", [incoming]))

    def test_apply_cli_uses_selected_inventory_plan_and_fresh_rollback(self):
        module = runpy.run_path(str(CLI))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory_path = root / "inventory.json"
            plan_path = root / "plan.json"
            self.write_json(inventory_path, inventory())
            desired = load_inventory(inventory_path)
            endpoint = {
                "profile_id": "core", "scheme": "http", "host": "127.0.0.1",
                "port": 7979, "tenant": "default",
            }
            state = {"banks": []}
            plan = build_plan(
                desired,
                {"profile_id": "core", "endpoint": endpoint, "state": state, "compatibility": [{
                    "check": "fake-adapter-contract",
                    "compatible": True,
                    "status": "pass",
                }]},
                {"idle": True, "active": []},
            )
            self.write_json(plan_path, plan.to_dict())
            adapter = FakeAdapter(endpoint=endpoint, state=state)
            args = module["argparse"].Namespace(
                inventory=str(inventory_path),
                profile="core",
                plan=str(plan_path),
                approval_digest=plan.plan_digest,
                token_env="HINDSIGHT_TEST_TOKEN",
                completion_marker=None,
            )
            output = StringIO()
            with (
                patch.dict(os.environ, {"HINDSIGHT_TEST_TOKEN": "local-test-token"}),
                patch.dict(module["apply_command"].__globals__, {"HttpAdapter": lambda **_kwargs: adapter}),
                redirect_stdout(output),
            ):
                self.assertEqual(module["apply_command"](args), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["status"], "applied")
            self.assertEqual(
                result["applied_action_ids"],
                [
                    "01-configure-profile-core",
                    "02-create-bank-engineering",
                    "03-configure-bank-engineering",
                    "04-set-auto-consolidation-engineering",
                    "05-set-memory-defense-engineering",
                ],
            )
            self.assertIn("create_rollback_bundle", [call["method"] for call in adapter.calls])
            methods = [call["method"] for call in adapter.calls]
            self.assertLess(methods.index("create_rollback_bundle"), methods.index("create_bank"))

    def test_apply_cli_rejects_mutation_base_actions_before_admin_backup(self):
        module = runpy.run_path(str(CLI))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory_path = root / "inventory.json"
            plan_path = root / "plan.json"
            self.write_json(inventory_path, inventory())
            desired = load_inventory(inventory_path)
            endpoint = {
                "profile_id": "core",
                "scheme": "http",
                "host": "127.0.0.1",
                "port": 7979,
                "tenant": "default",
            }
            base = build_plan(
                desired,
                {
                    "profile_id": "core",
                    "endpoint": endpoint,
                    "state": {"banks": []},
                    "compatibility": [
                        {
                            "check": "fake-adapter-contract",
                            "compatible": True,
                            "status": "pass",
                        }
                    ],
                },
                {"idle": True, "active": []},
            )
            empty_body = base.to_dict()
            empty_body.pop("plan_digest")
            empty_body["actions"] = []
            empty_base = replace(
                base,
                actions=(),
                plan_digest=digest(empty_body),
            )
            incoming_evidence = {
                "schema_version": 1,
                "artifact_digest": "4" * 64,
                "verification_receipt_digest": "7" * 64,
            }
            rollback_evidence = {
                "schema_version": 1,
                "artifact_digest": "5" * 64,
                "verification_receipt_digest": "8" * 64,
            }
            mutation = build_mutation_plan(
                empty_base,
                migration_run_id="run-1",
                migration_artifact_digest="3" * 64,
                rollback_archive_digest="5" * 64,
                rollback_restore_evidence_digest=digest(rollback_evidence),
                actions=[
                    {
                        "id": "migrate-1",
                        "kind": "migrate_bank",
                        "artifact_digest": "3" * 64,
                        "archive_digest": "4" * 64,
                        "restore_evidence_digest": digest(incoming_evidence),
                        "source_bank": {
                            "profile_id": "core",
                            "bank_id": "historical-candidate",
                        },
                        "target_bank": {
                            "profile_id": "core",
                            "bank_id": "engineering",
                        },
                    }
                ],
            )
            invalid = mutation.to_dict()
            invalid["base_plan"] = base.to_dict()
            invalid_body = dict(invalid)
            invalid_body.pop("plan_digest")
            invalid["plan_digest"] = digest(invalid_body)
            self.write_json(plan_path, invalid)
            args = module["argparse"].Namespace(
                inventory=str(inventory_path),
                plan=str(plan_path),
                token_env="HINDSIGHT_TEST_TOKEN",
            )

            with (
                patch.dict(
                    os.environ,
                    {"HINDSIGHT_TEST_TOKEN": "local-test-token"},
                ),
                patch.object(module["subprocess"], "run") as admin_run,
                self.assertRaisesRegex(
                    module["ApplyError"], "base plan.*actions"
                ),
            ):
                module["apply_command"](args)
            admin_run.assert_not_called()

    def test_apply_cli_routes_mutation_through_digest_selected_admin_archive(self):
        module = runpy.run_path(str(CLI))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory_path = root / "inventory.json"
            plan_path = root / "plan.json"
            marker = root / "artifacts" / "distillation-complete.marker"
            proposal = root / "proposal.md"
            value = inventory()
            value["migration"] = {"artifact_dir": str(marker.parent), "proposal_log": str(proposal)}
            self.write_json(inventory_path, value)
            desired = load_inventory(inventory_path)
            endpoint = {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}
            state = {"banks": []}
            base = build_plan(
                desired, {"profile_id": "core", "endpoint": endpoint, "state": state, "compatibility": [{
                    "check": "fake-adapter-contract",
                    "compatible": True,
                    "status": "pass",
                }]},
                {"idle": True, "active": []},
            )
            base_body = base.to_dict()
            base_body.pop("plan_digest")
            base_body["actions"] = []
            base = replace(
                base,
                actions=(),
                plan_digest=digest(base_body),
            )
            archive = root / "approved-bank.zip"
            archive.write_bytes(b"approved migration archive")
            archive_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            decoy_archive = root / "decoy-bank.zip"
            decoy_archive.write_bytes(b"decoy migration archive")
            rollback_payload = b"verified pre-state rollback archive"
            rollback_digest = hashlib.sha256(rollback_payload).hexdigest()
            rollback_archive = root / "pre-state-backup.zip"
            admin_executable = root / "hindsight-admin"
            admin_executable.write_text(
                f"#!{TRUSTED_TEST_PYTHON}\nraise SystemExit('test seam only')\n",
                encoding="utf-8",
            )
            admin_executable.chmod(0o700)
            migration_digest = "3" * 64
            evidence_path = root / "restore-evidence.json"
            incoming_evidence = {
                "schema_version": 1,
                "artifact_digest": archive_digest,
                "verification_receipt_digest": "7" * 64,
            }
            rollback_evidence = {
                "schema_version": 1,
                "artifact_digest": rollback_digest,
                "verification_receipt_digest": "8" * 64,
            }
            self.write_json(evidence_path, {
                archive_digest: incoming_evidence,
                rollback_digest: rollback_evidence,
            })
            evidence_path.chmod(0o600)
            self.assertEqual(evidence_path.stat().st_mode & 0o777, 0o600)
            marker.parent.mkdir()
            marker.write_text(
                f"run=run-1\nartifact={migration_digest}\n", encoding="utf-8"
            )
            proposal.write_text(
                f"## Migration complete\nrun=run-1\nartifact={migration_digest}\n",
                encoding="utf-8",
            )
            plan = build_mutation_plan(
                base, migration_run_id="run-1", migration_artifact_digest=migration_digest,
                rollback_archive_digest=rollback_digest,
                rollback_restore_evidence_digest=digest(rollback_evidence),
                actions=[{
                    "id": "migrate-1", "kind": "migrate_bank",
                    "artifact_digest": migration_digest,
                    "archive_digest": archive_digest,
                    "restore_evidence_digest": digest(incoming_evidence),
                    "source_bank": {
                        "profile_id": "core", "bank_id": "historical-candidate",
                    },
                    "target_bank": {
                        "profile_id": "core", "bank_id": "engineering",
                    },
                }],
                migration_gate=capture_migration_gate(marker, proposal),
            )
            self.write_json(plan_path, plan.to_dict())
            args = module["argparse"].Namespace(
                inventory=str(inventory_path), profile="core", plan=str(plan_path),
                approval_digest=plan.plan_digest, token_env="HINDSIGHT_TEST_TOKEN",
                completion_marker=str(marker), migration_archive=[],
                restore_evidence=str(evidence_path),
                rollback_archive=str(rollback_archive),
                admin_executable=str(admin_executable),
                state_dir=str(root / "control-state"),
            )
            args.migration_archive = [str(archive)]
            protected_inputs = (
                archive,
                inventory_path,
                plan_path,
                evidence_path,
                admin_executable,
                marker,
                proposal,
            )
            for protected in protected_inputs:
                with (
                    self.subTest(rollback_overlap=protected.name),
                    patch.dict(
                        os.environ,
                        {"HINDSIGHT_TEST_TOKEN": "local-test-token"},
                    ),
                    patch.dict(
                        module["apply_command"].__globals__,
                        {"HttpAdapter": lambda **_kwargs: FakeAdapter(
                            endpoint=endpoint, state=state,
                        )},
                    ),
                    patch.object(
                        module["subprocess"],
                        "run",
                        side_effect=AssertionError(
                            "overlapping apply input must fail before admin execution"
                        ),
                    ),
                ):
                    args.rollback_archive = str(protected)
                    with self.assertRaisesRegex(
                        module["ApplyError"], "distinct"
                    ):
                        module["apply_command"](args)
            args.rollback_archive = str(rollback_archive)
            for candidates in (
                (archive, decoy_archive),
                (decoy_archive, archive),
            ):
                with self.subTest(candidates=[path.name for path in candidates]):
                    rollback_archive.unlink(missing_ok=True)
                    adapter = FakeAdapter(endpoint=endpoint, state=state)
                    args.migration_archive = [str(path) for path in candidates]
                    admin_calls = []
                    process_contexts = []
                    observed_import = {}

                    def run_admin(argv, **_kwargs):
                        process_contexts.append(_kwargs)
                        if "HINDSIGHT_INTERPRETER_PREFIX_V1" in argv[-1]:
                            return interpreter_prefix_probe(argv)
                        if "HINDSIGHT_RUNTIME_MANIFEST_V1" in argv[-1]:
                            runtime_file = Path(__file__).resolve()
                            return subprocess.CompletedProcess(
                                argv,
                                0,
                                json.dumps({
                                    "root": str(runtime_file.parent),
                                    "files": [{
                                        "path": str(runtime_file),
                                        "relative": runtime_file.name,
                                    }],
                                }),
                                "",
                            )
                        if "importlib.metadata" in argv[-1]:
                            return subprocess.CompletedProcess(argv, 0, "0.8.4\n", "")
                        self.assertEqual(argv[0], TRUSTED_TEST_PYTHON)
                        self.assertEqual(argv[1], "-S")
                        self.assertRegex(argv[2], r"^/dev/fd/[0-9]+$")
                        execution_fd = int(argv[2].rsplit("/", 1)[1])
                        pass_fds = _kwargs.get("pass_fds")
                        self.assertIsNotNone(pass_fds)
                        self.assertEqual(pass_fds[0], execution_fd)
                        self.assertEqual(
                            Path(argv[2]).read_bytes(),
                            admin_executable.read_bytes(),
                        )
                        admin_argv = argv[3:]
                        admin_calls.append(admin_argv)
                        if admin_argv[0] == "backup":
                            self.assertEqual(pass_fds, (execution_fd,))
                            Path(admin_argv[1]).write_bytes(rollback_payload)
                            Path(admin_argv[1]).chmod(0o600)
                        if admin_argv[0] == "import-bank":
                            snapshot = Path(admin_argv[2])
                            self.assertRegex(str(snapshot), r"^/dev/fd/[0-9]+$")
                            archive_fd = int(str(snapshot).rsplit("/", 1)[1])
                            self.assertEqual(pass_fds, (execution_fd, archive_fd))
                            observed_import.update({
                                "path": snapshot,
                                "payload": snapshot.read_bytes(),
                                "mode": snapshot.stat().st_mode & 0o777,
                            })
                        return subprocess.CompletedProcess(argv, 0, "{}", "")

                    with (
                        patch.dict(os.environ, {
                            "HINDSIGHT_TEST_TOKEN": "local-test-token",
                            "HINDSIGHT_API_DATABASE_URL": "postgresql://approved",
                            "PYTHONPATH": "/attacker/python",
                        }),
                        patch.dict(module["apply_command"].__globals__, {"HttpAdapter": lambda **_kwargs: adapter}),
                        patch.object(module["subprocess"], "run", side_effect=run_admin),
                        redirect_stdout(StringIO()),
                    ):
                        self.assertEqual(module["apply_command"](args), 0)
                    self.assertEqual(admin_calls[0][0], "backup")
                    self.assertRegex(
                        admin_calls[0][1],
                        r"/\.hindsight-admin-output-[^/]+/archive\.zip$",
                    )
                    self.assertEqual(admin_calls[0][2:], ["--schema", "public"])
                    self.assertEqual(admin_calls[1], [
                        "import-bank",
                        "--archive", str(observed_import["path"]),
                        "--target-bank", "engineering",
                    ])
                    self.assertNotIn(observed_import["path"], candidates)
                    self.assertEqual(observed_import["payload"], archive.read_bytes())
                    self.assertEqual(observed_import["mode"], 0o400)
                    self.assertFalse(observed_import["path"].exists())
                    self.assertEqual(
                        archive.read_bytes(), b"approved migration archive",
                    )
                    self.assertEqual(
                        decoy_archive.read_bytes(), b"decoy migration archive",
                    )
                    self.assertNotIn(archive_digest, admin_calls[1])
                    self.assertTrue(process_contexts)
                    self.assertTrue(all(context["cwd"] == "/" for context in process_contexts))
                    self.assertTrue(all(
                        context["env"].get("PYTHONPATH") != "/attacker/python"
                        for context in process_contexts
                    ))
                    runtime_paths = [
                        context["env"]["PYTHONPATH"]
                        for context in process_contexts
                        if "PYTHONPATH" in context["env"]
                    ]
                    self.assertTrue(runtime_paths)
                    self.assertTrue(all(
                        Path(path).is_absolute()
                        and Path(path).name.startswith(
                            ".hindsight-admin-runtime-"
                        )
                        for path in runtime_paths
                    ))
                    self.assertTrue(all(
                        context["env"].get("HINDSIGHT_API_DATABASE_URL") == "postgresql://approved"
                        for context in process_contexts
                    ))

            evidence_path.write_bytes(b"\xff")
            evidence_path.chmod(0o600)
            adapter_calls_before = list(adapter.calls)
            with (
                patch.dict(
                    os.environ,
                    {"HINDSIGHT_TEST_TOKEN": "local-test-token"},
                ),
                patch.dict(
                    module["apply_command"].__globals__,
                    {"HttpAdapter": lambda **_kwargs: adapter},
                ),
                patch.object(
                    module["subprocess"],
                    "run",
                    side_effect=AssertionError(
                        "admin command must not run for invalid evidence"
                    ),
                ),
            ):
                with self.assertRaisesRegex(
                    module["ApplyError"],
                    "disposable restore evidence is unavailable",
                ):
                    module["apply_command"](args)
            self.assertEqual(adapter.calls, adapter_calls_before)

            self.write_json(evidence_path, {
                archive_digest: incoming_evidence,
                rollback_digest: rollback_evidence,
            })
            evidence_path.chmod(0o600)
            mismatched_backup_calls = []
            rollback_archive.unlink(missing_ok=True)

            def run_mismatched_backup(argv, **_kwargs):
                if "HINDSIGHT_INTERPRETER_PREFIX_V1" in argv[-1]:
                    return interpreter_prefix_probe(argv)
                if "HINDSIGHT_RUNTIME_MANIFEST_V1" in argv[-1]:
                    runtime_file = Path(__file__).resolve()
                    return subprocess.CompletedProcess(
                        argv,
                        0,
                        json.dumps({
                            "root": str(runtime_file.parent),
                            "files": [{
                                "path": str(runtime_file),
                                "relative": runtime_file.name,
                            }],
                        }),
                        "",
                    )
                if "importlib.metadata" in argv[-1]:
                    return subprocess.CompletedProcess(argv, 0, "0.8.4\n", "")
                self.assertEqual(argv[0], TRUSTED_TEST_PYTHON)
                self.assertEqual(argv[1], "-S")
                self.assertRegex(argv[2], r"^/dev/fd/[0-9]+$")
                self.assertEqual(
                    _kwargs.get("pass_fds"), (int(argv[2].rsplit("/", 1)[1]),)
                )
                self.assertEqual(
                    Path(argv[2]).read_bytes(),
                    admin_executable.read_bytes(),
                )
                admin_argv = argv[3:]
                mismatched_backup_calls.append(admin_argv)
                Path(admin_argv[1]).write_bytes(b"mismatched rollback archive")
                return subprocess.CompletedProcess(argv, 0, "Complete", "")

            with (
                patch.dict(
                    os.environ,
                    {"HINDSIGHT_TEST_TOKEN": "local-test-token"},
                ),
                patch.dict(
                    module["apply_command"].__globals__,
                    {"HttpAdapter": lambda **_kwargs: adapter},
                ),
                patch.object(
                    module["subprocess"],
                    "run",
                    side_effect=run_mismatched_backup,
                ),
            ):
                with self.assertRaisesRegex(
                    module["ApplyError"], "rollback archive.*digest",
                ):
                    module["apply_command"](args)
            self.assertEqual(len(mismatched_backup_calls), 1)
            self.assertEqual(mismatched_backup_calls[0][0], "backup")
            self.assertRegex(
                mismatched_backup_calls[0][1],
                r"/\.hindsight-admin-output-[^/]+/archive\.zip$",
            )
            self.assertEqual(
                mismatched_backup_calls[0][2:], ["--schema", "public"]
            )
            self.assertEqual(adapter.calls, adapter_calls_before)

    def test_broker_pid_read_is_bounded_and_disappearance_is_invalid(self):
        module = runpy.run_path(str(CLI))
        read_broker_pid = module["_read_broker_pid"]
        broker_error = module["BrokerError"]
        with tempfile.TemporaryDirectory() as directory:
            pid_path = Path(directory) / "broker.pid"
            pid_path.write_bytes(b"9" * (1024 * 1024))
            os.chmod(pid_path, 0o600)
            with patch.object(module["os"], "read", wraps=os.read) as read:
                with self.assertRaisesRegex(broker_error, "BROKER_PID_INVALID"):
                    read_broker_pid(pid_path)
            self.assertFalse(read.called)
            pid_path.write_bytes(b"not-a-pid")
            with patch.object(module["os"], "read", wraps=os.read) as read:
                with self.assertRaisesRegex(broker_error, "BROKER_PID_INVALID"):
                    read_broker_pid(pid_path)
            self.assertTrue(read.called)
            self.assertTrue(all(call.args[1] <= 257 for call in read.call_args_list))

        class DisappearingPath:
            def __fspath__(self):
                return "/definitely/missing/broker.pid"

            def lstat(self):
                return type(
                    "Metadata",
                    (),
                    {
                        "st_mode": stat.S_IFREG | 0o600,
                        "st_size": 3,
                        "st_uid": os.geteuid(),
                        "st_nlink": 1,
                    },
                )()

        with self.assertRaisesRegex(broker_error, "BROKER_PID_INVALID"):
            read_broker_pid(DisappearingPath())

    def test_broker_probe_requires_the_exact_health_response_schema(self):
        module = runpy.run_path(str(CLI))

        class SocketPath:
            def lstat(self):
                return type(
                    "Metadata",
                    (),
                    {"st_mode": stat.S_IFSOCK | 0o600},
                )()

            def __str__(self):
                return "/private/test/broker.sock"

        class Connection:
            def __init__(self, response):
                self.response = response

            def settimeout(self, _timeout):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def connect(self, _path):
                pass

            def sendall(self, _request):
                pass

            def makefile(self, _mode):
                return BytesIO(self.response)

        exact = json.dumps(module["BROKER_HEALTH_RESPONSE"]).encode() + b"\n"
        with patch.object(module["socket"], "socket", return_value=Connection(exact)):
            self.assertTrue(module["_broker_probe"](SocketPath()))
        with (
            patch.object(
                module["socket"], "socket", return_value=Connection(exact)
            ),
            patch.dict(
                module["_broker_probe"].__globals__,
                {"_broker_peer_pid": lambda _connection: 4321},
            ),
        ):
            self.assertTrue(
                module["_broker_probe"](SocketPath(), expected_pid=4321)
            )
            self.assertFalse(
                module["_broker_probe"](SocketPath(), expected_pid=1234)
            )

        permissive = b'{"jsonrpc":"2.0","id":"health","result":{}}\n'
        with patch.object(
            module["socket"], "socket", return_value=Connection(permissive)
        ):
            self.assertFalse(module["_broker_probe"](SocketPath()))

    def test_broker_status_requires_the_canonical_enabled_profile_set(self):
        module = runpy.run_path(str(CLI))
        args = module["argparse"].Namespace(
            state_dir="/private/test/state",
            socket="/private/test/broker.sock",
            profile=["second", "first", "second"],
        )
        expected_digest = module["digest"](
            {"mode": "inactive", "profiles": ["first", "second"]}
        )
        identity = module["BrokerProcessIdentity"](
            1234, "stable-start", "s" * 32, expected_digest
        )
        globals_patch = {
            "_broker_paths": lambda _args: (
                Path("/private/test/state"),
                Path("/private/test/broker.sock"),
                Path("/private/test/state/broker.pid"),
            ),
            "_private_broker_state": lambda _state, create=False: True,
            "_broker_probe": lambda _socket, **kwargs: (
                kwargs.get("expected_pid") == identity.pid
            ),
            "_read_broker_pid": lambda _pid: identity,
            "_process_identity_matches": lambda candidate: (
                candidate == identity
            ),
        }
        with (
            patch.dict(module["broker_status_command"].__globals__, globals_patch),
            redirect_stdout(StringIO()),
        ):
            self.assertEqual(module["broker_status_command"](args), 0)

        mismatched = identity._replace(profile_set_digest="0" * 64)
        globals_patch["_read_broker_pid"] = lambda _pid: mismatched
        with (
            patch.dict(module["broker_status_command"].__globals__, globals_patch),
            self.assertRaisesRegex(
                module["BrokerError"], "BROKER_PROFILE_SET_MISMATCH"
            ),
        ):
            module["broker_status_command"](args)

        globals_patch["_read_broker_pid"] = lambda _pid: None
        with (
            patch.dict(module["broker_status_command"].__globals__, globals_patch),
            self.assertRaisesRegex(module["BrokerError"], "BROKER_UNAVAILABLE"),
        ):
            module["broker_status_command"](args)

    def test_broker_pid_parser_rejects_values_outside_signal_range(self):
        module = runpy.run_path(str(CLI))
        invalid = 1 << 63
        for payload in (
            str(invalid).encode("ascii"),
            json.dumps({"pid": invalid, "start_time": "stable"}).encode("ascii"),
        ):
            with self.subTest(payload=payload), self.assertRaisesRegex(
                module["BrokerError"], "BROKER_PID_INVALID"
            ):
                module["_parse_broker_pid"](payload)
        with (
            patch.object(module["os"], "kill") as kill,
            self.assertRaisesRegex(module["BrokerError"], "BROKER_PID_INVALID"),
        ):
            module["_process_running"](invalid)
        kill.assert_not_called()

    def test_migration_discovery_uses_canonical_environment_names(self):
        module = runpy.run_path(str(CLI))
        args = module["argparse"].Namespace(
            read_only=True,
            token_env="lowercase_token",
        )
        with self.assertRaisesRegex(
            module["MigrationError"], "token environment locator is invalid"
        ):
            module["migration_discover_command"](args)

    def test_broker_termination_prefers_pidfd_after_identity_revalidation(self):
        module = runpy.run_path(str(CLI))
        identity = module["BrokerProcessIdentity"](
            1234, "stable-start", "s" * 32
        )
        order = []

        def identity_matches(_identity):
            order.append("identity")
            return True

        def signal_pidfd(*_args):
            order.append("signal")

        with (
            patch.object(module["os"], "pidfd_open", return_value=19, create=True),
            patch.object(
                module["signal"], "pidfd_send_signal",
                side_effect=signal_pidfd, create=True
            ) as send_signal,
            patch.object(module["os"], "close") as close,
            patch.dict(
                module["_terminate_broker_process"].__globals__,
                {"_process_identity_matches": identity_matches},
            ),
            patch.object(module["os"], "kill") as kill,
        ):
            module["_terminate_broker_process"](
                identity, Path("/private/test/broker.sock"), timeout_seconds=1
            )
        send_signal.assert_called_once_with(19, signal.SIGTERM)
        close.assert_called_once_with(19)
        kill.assert_not_called()
        self.assertEqual(order, ["identity", "signal"])

    def test_broker_termination_uses_shutdown_rpc_without_pidfd(self):
        module = runpy.run_path(str(CLI))
        identity = module["BrokerProcessIdentity"](
            1234, "stable-start", "s" * 32
        )
        calls = []

        class Client:
            def __init__(self, path, *, timeout_seconds):
                calls.append((path, timeout_seconds))

            def broker_shutdown(self, capability):
                calls.append(("shutdown", capability))
                return {"stopping": True}

        socket_path = Path("/private/test/broker.sock")
        with (
            patch.object(module["os"], "pidfd_open", None, create=True),
            patch.object(module["signal"], "pidfd_send_signal", None, create=True),
            patch.dict(
                module["_terminate_broker_process"].__globals__,
                {
                    "_process_start_time": lambda _pid: "stable-start",
                    "JsonRpcClient": Client,
                },
            ),
            patch.object(module["os"], "kill") as kill,
        ):
            module["_terminate_broker_process"](
                identity, socket_path, timeout_seconds=2
            )
        self.assertEqual(
            calls, [(socket_path, 2), ("shutdown", "s" * 32)]
        )
        kill.assert_not_called()

    def test_broker_shutdown_revalidates_start_identity_after_client_setup(self):
        module = runpy.run_path(str(CLI))
        identity = module["BrokerProcessIdentity"](
            1234, "stable-start", "s" * 32
        )

        class Client:
            def __init__(self, _path, *, timeout_seconds):
                self.timeout_seconds = timeout_seconds

            def broker_shutdown(self, _capability):
                self.fail("shutdown must not run after identity drift")

        with (
            patch.object(module["os"], "pidfd_open", None, create=True),
            patch.object(
                module["signal"], "pidfd_send_signal", None, create=True
            ),
            patch.dict(
                module["_terminate_broker_process"].__globals__,
                {
                    "_process_start_time": Mock(
                        side_effect=(
                            "stable-start", "stable-start", "replacement"
                        )
                    ),
                    "_process_running": lambda _pid: True,
                    "JsonRpcClient": Client,
                },
            ),
            self.assertRaisesRegex(
                module["BrokerError"], "BROKER_PID_IDENTITY_INVALID"
            ),
        ):
            module["_terminate_broker_process"](
                identity, Path("/private/test/broker.sock"), timeout_seconds=2
            )

    def test_broker_state_and_identity_files_require_owner_and_single_link(self):
        module = runpy.run_path(str(CLI))
        broker_error = module["BrokerError"]
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            signing_key = state / "broker.signing-key"
            signing_key.write_bytes(b"k" * 32)
            signing_key.chmod(0o600)
            signing_key_link = state / "broker.signing-key-link"
            os.link(signing_key, signing_key_link)
            with self.assertRaisesRegex(broker_error, "SIGNING_KEY_INVALID"):
                module["_read_private"](
                    signing_key, 32, "SIGNING_KEY_INVALID"
                )

            pid_path = state / "broker.pid"
            pid_path.write_text(
                json.dumps({"pid": 12345, "start_time": "process-start"}),
                encoding="ascii",
            )
            pid_path.chmod(0o600)
            pid_link = state / "broker.pid-link"
            os.link(pid_path, pid_link)
            with self.assertRaisesRegex(broker_error, "BROKER_PID_INVALID"):
                module["_read_broker_pid"](pid_path)

            actual_uid = os.geteuid()
            with patch.object(module["os"], "geteuid", return_value=actual_uid + 1):
                with self.assertRaisesRegex(broker_error, "BROKER_PATH_INVALID"):
                    module["_private_broker_state"](state)

    def test_broker_private_reads_reject_unsafe_or_symlinked_ancestors(self):
        module = runpy.run_path(str(CLI))
        broker_error = module["BrokerError"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unsafe = root / "unsafe"
            unsafe.mkdir(mode=0o700)
            state = unsafe / "state"
            state.mkdir(mode=0o700)
            pid_path = state / "broker.pid"
            pid_path.write_text("12345", encoding="ascii")
            pid_path.chmod(0o600)
            unsafe.chmod(0o733)
            try:
                with self.assertRaisesRegex(broker_error, "BROKER_PID_INVALID"):
                    module["_read_broker_pid"](pid_path)
                with self.assertRaisesRegex(broker_error, "BROKER_PATH_INVALID"):
                    module["_private_broker_state"](state, create=False)
            finally:
                unsafe.chmod(0o700)
            alias = root / "state-alias"
            alias.symlink_to(state, target_is_directory=True)
            with self.assertRaisesRegex(broker_error, "BROKER_PID_INVALID"):
                module["_read_broker_pid"](alias / "broker.pid")

    def test_broker_pid_read_is_pinned_across_ancestor_replacement(self):
        module = runpy.run_path(str(CLI))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ancestor = root / "active"
            original_state = ancestor / "state"
            original_state.mkdir(parents=True, mode=0o700)
            original = {
                "pid": 12345,
                "start_time": "original-process-start",
            }
            original_pid = original_state / "broker.pid"
            original_pid.write_text(json.dumps(original), encoding="ascii")
            original_pid.chmod(0o600)

            replacement = root / "replacement"
            replacement_state = replacement / "state"
            replacement_state.mkdir(parents=True, mode=0o700)
            replacement_pid = replacement_state / "broker.pid"
            replacement_pid.write_text(
                json.dumps(
                    {
                        "pid": 54321,
                        "start_time": "replacement-process-start",
                    }
                ),
                encoding="ascii",
            )
            replacement_pid.chmod(0o600)
            displaced = root / "displaced"
            real_open = os.open
            replaced = False

            def replace_before_private_open(path, *args, **kwargs):
                nonlocal replaced
                if path == "broker.pid" and not replaced:
                    replaced = True
                    ancestor.rename(displaced)
                    replacement.rename(ancestor)
                return real_open(path, *args, **kwargs)

            with patch.object(
                module["os"], "open", side_effect=replace_before_private_open
            ):
                identity = module["_read_broker_pid"](original_pid)

            self.assertTrue(replaced)
            self.assertEqual(
                identity,
                module["BrokerProcessIdentity"](
                    original["pid"], original["start_time"]
                ),
            )
            self.assertIn(
                "replacement-process-start",
                original_pid.read_text(encoding="ascii"),
            )

    def test_failed_broker_pid_write_removes_the_partial_file(self):
        module = runpy.run_path(str(CLI))
        with tempfile.TemporaryDirectory() as directory:
            pid_path = Path(directory) / "broker.pid"
            with patch.object(module["os"], "write", return_value=0):
                with self.assertRaises(OSError):
                    module["_write_broker_pid"](pid_path)
            self.assertFalse(pid_path.exists())

            def replace_pid(_descriptor, _body):
                pid_path.unlink()
                pid_path.write_text("replacement", encoding="utf-8")
                return 0

            with patch.object(module["os"], "write", side_effect=replace_pid):
                with self.assertRaises(OSError):
                    module["_write_broker_pid"](pid_path)
            self.assertEqual(pid_path.read_text(encoding="utf-8"), "replacement")

    def test_broker_signing_key_requires_directory_durability(self):
        module = runpy.run_path(str(CLI))
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            calls = 0

            def fail_directory_fsync(_descriptor):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("directory fsync failed")

            with (
                patch.object(module["os"], "fsync", side_effect=fail_directory_fsync),
                self.assertRaisesRegex(OSError, "directory fsync failed"),
            ):
                module["_broker_signing_key"](state)
            self.assertGreaterEqual(calls, 3)
            self.assertFalse((state / "broker.signing-key").exists())

    def test_failed_private_artifact_write_removes_only_its_partial_file(self):
        module = runpy.run_path(str(CLI))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "plan.json"
            with patch.object(module["os"], "write", return_value=0):
                with self.assertRaises(OSError):
                    module["write_private"](target, {"schema_version": 1})
            self.assertFalse(target.exists())

            target.write_text("previous", encoding="utf-8")
            with patch.object(module["os"], "write", return_value=0):
                with self.assertRaises(OSError):
                    module["write_private"](target, {"schema_version": 1})
            self.assertEqual(target.read_text(encoding="utf-8"), "previous")

            def replace_target(_descriptor, _body):
                target.unlink()
                target.write_text("replacement", encoding="utf-8")
                return 0

            with patch.object(module["os"], "write", side_effect=replace_target):
                with self.assertRaises(OSError):
                    module["write_private"](target, {"schema_version": 1})
            self.assertEqual(target.read_text(encoding="utf-8"), "replacement")

    def test_create_only_private_artifact_never_replaces_a_reservation(self):
        module = runpy.run_path(str(CLI))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "external-action.json"
            module["write_private"](
                target, {"status": "indeterminate"}, create_only=True
            )
            with self.assertRaises(FileExistsError):
                module["write_private"](
                    target, {"status": "replacement"}, create_only=True
                )
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"status": "indeterminate"},
            )

    def test_uncertain_private_exchange_rollback_preserves_displaced_original(self):
        module = runpy.run_path(str(CLI))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "plan.json"
            target.write_text("previous", encoding="utf-8")
            calls = 0
            identity_calls = 0
            real_identity = module["_directory_entry_identity"]

            def exchange(_directory, source, destination, _flag):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("rollback outcome unknown")
                source_path = root / source
                destination_path = root / destination
                swap = root / ".exchange-swap"
                source_path.rename(swap)
                destination_path.rename(source_path)
                swap.rename(destination_path)

            def uncertain_identity(directory_descriptor, name):
                nonlocal identity_calls
                identity_calls += 1
                if identity_calls == 3:
                    raise OSError("post-exchange identity unavailable")
                return real_identity(directory_descriptor, name)

            with (
                patch.dict(
                    module["_commit_private_artifact"].__globals__,
                    {
                        "_atomic_rename": exchange,
                        "_directory_entry_identity": uncertain_identity,
                    },
                ),
                self.assertRaisesRegex(OSError, "rollback is uncertain"),
            ):
                module["write_private"](target, {"schema_version": 1})

            displaced = list(root.glob(".plan.json.*.tmp"))
            self.assertEqual(len(displaced), 1)
            self.assertEqual(displaced[0].read_text(encoding="utf-8"), "previous")
            self.assertEqual(json.loads(target.read_text()), {"schema_version": 1})

    def test_private_artifact_parent_requires_private_current_user_directory(self):
        module = runpy.run_path(str(CLI))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public_parent = root / "public-parent"
            public_parent.mkdir(mode=0o750)
            with self.assertRaisesRegex(OSError, "current-user-owned and private"):
                module["write_private"](
                    public_parent / "plan.json", {"schema_version": 1}
                )

    def test_private_artifact_parent_rejects_writable_ancestor(self):
        module = runpy.run_path(str(CLI))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writable_ancestor = root / "writable-ancestor"
            writable_ancestor.mkdir(mode=0o775)
            writable_ancestor.chmod(0o775)
            private_parent = writable_ancestor / "private-parent"
            private_parent.mkdir(mode=0o700)
            with self.assertRaisesRegex(OSError, "ancestry is untrusted"):
                module["write_private"](
                    private_parent / "plan.json", {"schema_version": 1}
                )

    def test_broker_serve_refuses_a_live_pid_when_the_probe_is_unhealthy(self):
        module = runpy.run_path(str(CLI))
        broker_error = module["BrokerError"]
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            pid_path = state / "broker.pid"
            pid_path.write_text(
                json.dumps({"pid": 12345, "start_time": "process-start"}),
                encoding="ascii",
            )
            os.chmod(pid_path, 0o600)
            args = module["argparse"].Namespace(
                state_dir=str(state),
                socket=str(state / "broker.sock"),
                profile=["example"],
                shutdown_timeout=1.0,
            )
            with patch.dict(
                module["broker_serve_command"].__globals__,
                {
                    "_process_running": lambda _pid: True,
                    "_process_start_time": lambda _pid: "process-start",
                    "_broker_probe": lambda _path, timeout=1.0: False,
                },
            ):
                with self.assertRaisesRegex(broker_error, "BROKER_ALREADY_RUNNING"):
                    module["broker_serve_command"](args)
            self.assertIn("process-start", pid_path.read_text(encoding="ascii"))

    def test_broker_serve_retains_pid_after_close_failure(self):
        module = runpy.run_path(str(CLI))
        events = []

        class StoppedEvent:
            def wait(self, _timeout):
                return True

            def set(self):
                pass

        class FailingServer:
            def __init__(self, _socket_path, _broker, **_kwargs):
                pass

            def start(self):
                pass

            def close(self):
                events.append("close")
                raise RuntimeError("close failed")

        class RecordingBroker:
            def __init__(self, **_kwargs):
                pass

            def shutdown(self, *, timeout_seconds):
                events.append(("shutdown", timeout_seconds))
                return {
                    "undrained": 0,
                    "active_reads": 0,
                    "active_writes": 0,
                    "retired": False,
                    "retirement_pending": False,
                }

        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            args = module["argparse"].Namespace(
                state_dir=str(state),
                socket=str(state / "broker.sock"),
                profile=["example"],
                shutdown_timeout=1.25,
            )
            with patch.dict(
                module["broker_serve_command"].__globals__,
                {
                    "Broker": RecordingBroker,
                    "UnixJsonRpcServer": FailingServer,
                    "_process_start_time": lambda _pid: "process-start",
                },
            ), patch.object(module["threading"], "Event", StoppedEvent):
                broker_error = module["BrokerError"]
                with self.assertRaisesRegex(
                    broker_error,
                    "BROKER_SERVER_CLOSE_FAILED",
                ):
                    module["broker_serve_command"](args)

            self.assertEqual(events, ["close", ("shutdown", 1.25)])
            self.assertTrue((state / "broker.pid").exists())

    def test_broker_serve_retains_pid_until_shutdown_is_drained(self):
        module = runpy.run_path(str(CLI))

        class StoppedEvent:
            def wait(self, _timeout):
                return True

            def set(self):
                pass

        class Server:
            def __init__(self, _socket_path, _broker, **_kwargs):
                pass

            def start(self):
                pass

            def close(self):
                pass

        class UndrainedBroker:
            def __init__(self, **_kwargs):
                pass

            def shutdown(self, *, timeout_seconds):
                return {
                    "undrained": 1,
                    "active_reads": 0,
                    "active_writes": 0,
                    "retired": False,
                    "retirement_pending": False,
                }

        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            args = module["argparse"].Namespace(
                state_dir=str(state),
                socket=str(state / "broker.sock"),
                profile=["example"],
                shutdown_timeout=1.0,
            )
            with (
                patch.dict(
                    module["broker_serve_command"].__globals__,
                    {
                        "Broker": UndrainedBroker,
                        "UnixJsonRpcServer": Server,
                        "_process_start_time": lambda _pid: "process-start",
                    },
                ),
                patch.object(module["threading"], "Event", StoppedEvent),
                self.assertRaisesRegex(
                    module["BrokerError"], "BROKER_SHUTDOWN_INCOMPLETE"
                ),
            ):
                module["broker_serve_command"](args)
            self.assertTrue((state / "broker.pid").exists())

    def test_broker_serve_reports_secure_pid_cleanup_failure(self):
        module = runpy.run_path(str(CLI))
        broker_error = module["BrokerError"]

        class StoppedEvent:
            def wait(self, _timeout):
                return True

            def set(self):
                pass

        class Server:
            def __init__(self, _socket_path, _broker, **_kwargs):
                pass

            def start(self):
                pass

            def close(self):
                pass

        class RecordingBroker:
            def __init__(self, **_kwargs):
                pass

            def shutdown(self, *, timeout_seconds):
                return {
                    "undrained": 0,
                    "active_reads": 0,
                    "active_writes": 0,
                    "retired": False,
                    "retirement_pending": False,
                }

        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            args = module["argparse"].Namespace(
                state_dir=str(state),
                socket=str(state / "broker.sock"),
                profile=["example"],
                shutdown_timeout=1.0,
            )
            identity = module["BrokerProcessIdentity"](
                os.getpid(), "process-start", "shutdown-capability"
            )
            with (
                patch.dict(
                    module["broker_serve_command"].__globals__,
                    {
                        "Broker": RecordingBroker,
                        "UnixJsonRpcServer": Server,
                        "_read_broker_pid_entry": Mock(
                            side_effect=(
                                None,
                                broker_error("BROKER_PID_INVALID"),
                            )
                        ),
                        "_write_broker_pid_entry": Mock(
                            return_value=identity
                        ),
                    },
                ),
                patch.object(module["threading"], "Event", StoppedEvent),
                self.assertRaisesRegex(broker_error, "BROKER_PID_INVALID"),
            ):
                module["broker_serve_command"](args)

    def test_broker_stop_rejects_a_reused_pid_without_signaling(self):
        module = runpy.run_path(str(CLI))
        broker_error = module["BrokerError"]
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            pid_path = state / "broker.pid"
            pid_path.write_text(
                json.dumps({"pid": 12345, "start_time": "original-start"}),
                encoding="ascii",
            )
            os.chmod(pid_path, 0o600)
            args = module["argparse"].Namespace(
                state_dir=str(state),
                socket=str(state / "broker.sock"),
                timeout=0.0,
            )
            with (
                patch.dict(
                    module["broker_stop_command"].__globals__,
                    {
                        "_process_running": lambda _pid: True,
                        "_process_start_time": lambda _pid: "replacement-start",
                    },
                ),
                patch.object(module["os"], "kill") as kill,
            ):
                with self.assertRaisesRegex(broker_error, "BROKER_PID_IDENTITY_INVALID"):
                    module["broker_stop_command"](args)
            self.assertTrue(
                all(call.args[1] == 0 for call in kill.call_args_list)
            )
            self.assertTrue(pid_path.exists())

    def test_broker_commands_reject_non_finite_or_out_of_range_timeouts(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            for arguments in (
                (
                    "broker", "stop", "--socket", state / "broker.sock",
                    "--timeout", "nan",
                ),
                (
                    "broker", "stop", "--socket", state / "broker.sock",
                    "--timeout", "31",
                ),
                (
                    "broker", "serve", "--socket", state / "broker.sock",
                    "--profile", "example", "--shutdown-timeout", "inf",
                ),
            ):
                with self.subTest(arguments=arguments):
                    result = self.run_cli(state, *arguments)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("BROKER_TIMEOUT_INVALID", result.stderr)
            self.assertFalse(state.exists())

    def test_capability_rpc_rejects_socket_outside_state_directory(self):
        module = runpy.run_path(str(CLI))
        args = module["argparse"].Namespace(
            state_dir="/private/tmp/hindsight-state",
            socket="/private/tmp/other/broker.sock",
            timeout=1.0,
        )
        with self.assertRaisesRegex(module["BrokerError"], "BROKER_PATH_INVALID"):
            module["_json_rpc_client"](args)

    def test_capability_rpc_validates_pid_before_constructing_client(self):
        module = runpy.run_path(str(CLI))
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            args = module["argparse"].Namespace(
                state_dir=str(state),
                socket=str(state / "broker.sock"),
                timeout=1.0,
            )
            constructed = Mock()
            with (
                patch.dict(
                    module["_json_rpc_client"].__globals__,
                    {
                        "_read_broker_pid_entry": Mock(return_value=None),
                        "JsonRpcClient": constructed,
                    },
                ),
                self.assertRaisesRegex(
                    module["BrokerError"], "BROKER_PID_IDENTITY_INVALID"
                ),
            ):
                module["_json_rpc_client"](args)
            constructed.assert_not_called()

    def test_broker_stop_removes_a_stale_pid_file(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            pid_path = state / "broker.pid"
            pid_path.write_text("999999999", encoding="ascii")
            os.chmod(pid_path, 0o600)
            result = self.run_cli(
                state,
                "broker", "stop",
                "--socket", state / "broker.sock",
                "--timeout", "0.1",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(pid_path.exists())

    def test_broker_stop_binds_outer_and_locked_probes_to_recorded_pid(self):
        module = runpy.run_path(str(CLI))
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            pid_path = state / "broker.pid"
            pid_path.write_text(
                json.dumps({"pid": 12345, "start_time": "process-start"}),
                encoding="ascii",
            )
            os.chmod(pid_path, 0o600)
            args = module["argparse"].Namespace(
                state_dir=str(state),
                socket=str(state / "broker.sock"),
                timeout=1.0,
            )
            identity_matches = Mock(side_effect=(True, False, False))
            probe = Mock(return_value=False)
            with patch.dict(
                module["broker_stop_command"].__globals__,
                {
                    "_process_running": lambda _pid: True,
                    "_process_identity_matches": identity_matches,
                    "_terminate_broker_process": Mock(),
                    "_broker_probe": probe,
                },
            ):
                self.assertEqual(module["broker_stop_command"](args), 0)

            self.assertEqual(probe.call_count, 2)
            self.assertTrue(
                all(
                    call.kwargs.get("expected_pid") == 12345
                    for call in probe.call_args_list
                )
            )

    def test_broker_stop_fails_closed_when_shutdown_rpc_is_unavailable(self):
        module = runpy.run_path(str(CLI))
        broker_error = module["BrokerError"]
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            pid_path = state / "broker.pid"
            pid_path.write_text(
                json.dumps({"pid": 12345, "start_time": "process-start"}),
                encoding="ascii",
            )
            os.chmod(pid_path, 0o600)

            args = module["argparse"].Namespace(
                state_dir=str(state),
                socket=str(state / "broker.sock"),
                timeout=0.0,
            )
            with (
                patch.dict(
                    module["broker_stop_command"].__globals__,
                    {
                        "_process_start_time": lambda _pid: "process-start",
                        "JsonRpcClient": lambda *_args, **_kwargs: type(
                            "UnavailableClient",
                            (),
                            {
                                "broker_shutdown": lambda self, _capability: (_ for _ in ()).throw(
                                    broker_error("BROKER_UNAVAILABLE")
                                )
                            },
                        )(),
                    },
                ),
                patch.object(module["os"], "pidfd_open", None, create=True),
                patch.object(
                    module["signal"], "pidfd_send_signal", None, create=True
                ),
                patch.object(module["os"], "kill") as kill,
            ):
                with self.assertRaisesRegex(
                    broker_error, "BROKER_SHUTDOWN_UNAVAILABLE"
                ):
                    module["broker_stop_command"](args)
            self.assertTrue(
                all(call.args[1] == 0 for call in kill.call_args_list)
            )

    def test_broker_stop_refuses_state_outside_a_private_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o755)
            socket_path = state / "broker.sock"
            socket_path.write_text("preserve", encoding="utf-8")
            result = self.run_cli(
                state,
                "broker", "stop",
                "--socket", socket_path,
                "--timeout", "0.1",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(socket_path.read_text(encoding="utf-8"), "preserve")

    def test_broker_stop_refuses_non_socket_cleanup_paths(self):
        for path_kind in ("regular", "symlink"):
            with self.subTest(path_kind=path_kind), tempfile.TemporaryDirectory() as directory:
                state = Path(directory) / "state"
                state.mkdir(mode=0o700)
                socket_path = state / "broker.sock"
                protected = state / "protected"
                if path_kind == "regular":
                    socket_path.write_text("preserve", encoding="utf-8")
                else:
                    protected.write_text("preserve", encoding="utf-8")
                    socket_path.symlink_to(protected)
                result = self.run_cli(
                    state,
                    "broker", "stop",
                    "--socket", socket_path,
                    "--timeout", "0.1",
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("BROKER_PATH_INVALID", result.stderr)
                if path_kind == "regular":
                    self.assertEqual(socket_path.read_text(encoding="utf-8"), "preserve")
                else:
                    self.assertTrue(socket_path.is_symlink())
                    self.assertEqual(protected.read_text(encoding="utf-8"), "preserve")

    def test_broker_socket_cannot_alias_private_state_artifacts(self):
        module = runpy.run_path(str(CLI))
        broker_error = module["BrokerError"]
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            for name in ("broker.pid", "broker.lock", "broker.signing-key"):
                with self.subTest(name=name), self.assertRaisesRegex(
                    broker_error, "BROKER_PATH_INVALID"
                ):
                    module["_broker_paths"](
                        module["argparse"].Namespace(
                            state_dir=str(state), socket=str(state / name)
                        )
                    )

    def test_broker_paths_return_canonical_state_and_socket(self):
        module = runpy.run_path(str(CLI))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            state.mkdir(mode=0o700)
            alias = root / "state-alias"
            alias.symlink_to(state, target_is_directory=True)
            resolved = module["_broker_paths"](
                module["argparse"].Namespace(
                    state_dir=str(alias),
                    socket=str(alias / "broker.sock"),
                )
            )
        self.assertEqual(
            resolved,
            (
                state.resolve(),
                state.resolve() / "broker.sock",
                state.resolve() / "broker.pid",
            ),
        )

    def test_private_artifact_writes_refuse_symlink_destinations(self):
        module = runpy.run_path(str(CLI))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            protected = root / "protected.json"
            protected.write_text("preserve", encoding="utf-8")
            destination = root / "plan.json"
            destination.symlink_to(protected)
            with self.assertRaises(OSError):
                module["write_private"](destination, {"schema_version": 1})
            self.assertEqual(protected.read_text(encoding="utf-8"), "preserve")

            real_parent = root / "real-parent"
            real_parent.mkdir()
            linked_parent = root / "linked-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            with self.assertRaises(OSError):
                module["write_private"](
                    linked_parent / "plan.json",
                    {"schema_version": 1},
                )
            self.assertFalse((real_parent / "plan.json").exists())

    def test_validate_is_closed_and_reports_a_known_canonical_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            compact = tmp / "compact.json"
            pretty = tmp / "pretty.json"
            self.write_json(compact, inventory())
            self.write_json(pretty, inventory(), pretty=True)

            expected = "eb2f5ccbf964bc384846e817bf741398c6545434b8ece93c49f5ee27ed915bdd"
            for fixture in (compact, pretty):
                result = self.run_cli(tmp, "validate", "--inventory", fixture)
                self.assertEqual(result.returncode, 0, result.stderr)
                output = json.loads(result.stdout)
                self.assertEqual(output["inventory_digest"], expected)
                self.assertRegex(output["artifact_digest"], r"^[0-9a-f]{64}$")

            for key in ("policy",):
                invalid = inventory()
                del invalid[key]
                fixture = tmp / f"missing-{key}.json"
                self.write_json(fixture, invalid)
                result = self.run_cli(tmp, "validate", "--inventory", fixture)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("root keys", result.stderr)

            invalid = inventory()
            invalid["surprise"] = True
            fixture = tmp / "unknown.json"
            self.write_json(fixture, invalid)
            result = self.run_cli(tmp, "validate", "--inventory", fixture)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("root keys", result.stderr)

            invalid = inventory()
            invalid["banks"].append(dict(invalid["banks"][0]))
            fixture = tmp / "duplicate.json"
            self.write_json(fixture, invalid)
            result = self.run_cli(tmp, "validate", "--inventory", fixture)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("duplicate canonical bank reference", result.stderr)

    def test_validate_rejects_references_authority_ports_placement_and_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            invalid_cases = []

            disabled_reference = inventory()
            disabled_reference["profiles"][0]["enabled"] = False
            disabled_reference["profiles"][0]["roles"]["llm"] = "missing-provider"
            invalid_cases.append((disabled_reference, "unknown provider"))

            split_brain = inventory()
            split_brain["banks"].append({"id": "engineering-2", "profile_id": "core", "data_class": "engineering", "authority": "authoritative", "writable": True})
            invalid_cases.append((split_brain, "exactly one authoritative write bank"))

            writable_replica = inventory()
            writable_replica["banks"][0]["authority"] = "replica"
            invalid_cases.append(
                (
                    writable_replica,
                    "writable engineering banks must be authoritative",
                )
            )

            missing_write_bank = inventory()
            del missing_write_bank["harnesses"][0]["write_bank"]
            invalid_cases.append((missing_write_bank, "requires write_bank"))

            unwritable_write_bank = inventory()
            unwritable_write_bank["policy"]["engineering_memory_enabled"] = False
            unwritable_write_bank["banks"][0]["writable"] = False
            invalid_cases.append(
                (unwritable_write_bank, "must reference a writable bank")
            )

            policy_disabled_write_bank = inventory()
            policy_disabled_write_bank["policy"][
                "engineering_memory_enabled"
            ] = False
            invalid_cases.append(
                (policy_disabled_write_bank, "disabled by engineering memory policy")
            )

            collision = inventory()
            collision["profiles"].append({**collision["profiles"][0], "id": "other", "slot": 1, "port": 7979})
            invalid_cases.append((collision, "endpoint collision"))

            canonical_collision = inventory()
            canonical_collision["profiles"][0].update(
                {"scheme": "https", "host": "Provider.Invalid.", "port": 443}
            )
            canonical_collision["profiles"].append(
                {
                    **canonical_collision["profiles"][0],
                    "id": "other",
                    "slot": 1,
                    "host": "provider.invalid",
                }
            )
            invalid_cases.append((canonical_collision, "endpoint collision"))

            forbidden = inventory()
            forbidden["providers"][0]["placement"] = "third-party-hosted"
            invalid_cases.append((forbidden, "placement is forbidden"))

            missing_placement_class = inventory()
            del missing_placement_class["policy"]["allowed_placements"][
                "engineering"
            ]
            invalid_cases.append(
                (missing_placement_class, "missing data class engineering")
            )

            foreign_harness_bank = inventory()
            foreign_harness_bank["profiles"].append({
                **foreign_harness_bank["profiles"][0],
                "id": "disabled",
                "slot": 1,
                "enabled": False,
            })
            foreign_harness_bank["banks"].append({
                "id": "engineering-copy",
                "profile_id": "disabled",
                "data_class": "engineering",
                "authority": "replica",
                "writable": False,
            })
            foreign_harness_bank["harnesses"][0]["home_bank"] = {
                "profile_id": "disabled",
                "bank_id": "engineering-copy",
            }
            invalid_cases.append(
                (foreign_harness_bank, "must belong to profile core")
            )

            relative_path = inventory()
            relative_path["migration"]["artifact_dir"] = "artifacts"
            invalid_cases.append((relative_path, "path must be absolute"))

            home_relative_paths = inventory()
            home_relative_paths["migration"]["artifact_dir"] = (
                "~/hindsight-artifacts"
            )
            invalid_cases.append(
                (home_relative_paths, "path must be absolute")
            )

            home_relative_proposal = inventory()
            home_relative_proposal["migration"]["proposal_log"] = (
                "~/hindsight-proposals.md"
            )
            invalid_cases.append(
                (home_relative_proposal, "path must be absolute")
            )

            for host in (
                "user@example.com",
                "example.com/path",
                "example.com?query",
                "example.com#fragment",
                "[::1]",
            ):
                invalid_host = inventory()
                invalid_host["profiles"][0].update(
                    {"scheme": "https", "host": host, "port": 443}
                )
                invalid_cases.append(
                    (invalid_host, "bare DNS name or IP literal")
                )

            missing_bank_class = inventory()
            del missing_bank_class["banks"][0]["data_class"]
            invalid_cases.append((missing_bank_class, "data_class must be one of"))

            unsupported_bank_class = inventory()
            unsupported_bank_class["banks"][0]["data_class"] = "unknown"
            invalid_cases.append((unsupported_bank_class, "data_class must be one of"))

            implicit_bank_class = inventory()
            implicit_bank_class["profiles"][0]["data_classes"] = []
            implicit_bank_class["providers"][0]["data_classes"] = ["personal"]
            invalid_cases.append((implicit_bank_class, "cannot receive engineering data"))

            bad_tls_profile = inventory()
            bad_tls_profile["policy"]["approved_tls_endpoints"] = [{
                "profile_id": "missing",
                "scheme": "https",
                "host": "provider.invalid",
                "port": 443,
                "tenant": "default",
            }]
            invalid_cases.append((bad_tls_profile, "approved TLS endpoint is invalid"))

            bad_tls_port = inventory()
            bad_tls_port["policy"]["approved_tls_endpoints"] = [{
                "profile_id": "core",
                "scheme": "https",
                "host": "provider.invalid",
                "port": 70000,
                "tenant": "default",
            }]
            invalid_cases.append((bad_tls_port, "approved TLS endpoint is invalid"))

            bad_tls_host = inventory()
            bad_tls_host["profiles"][0].update({
                "scheme": "https",
                "host": "provider.invalid",
                "port": 443,
            })
            bad_tls_host["policy"]["approved_tls_endpoints"] = [{
                "profile_id": "core",
                "scheme": "https",
                "host": "user@provider.invalid",
                "port": 443,
                "tenant": "default",
            }]
            invalid_cases.append(
                (bad_tls_host, "approved TLS endpoint is invalid")
            )

            for index, (value, message) in enumerate(invalid_cases):
                fixture = tmp / f"invalid-{index}.json"
                self.write_json(fixture, value)
                result = self.run_cli(tmp, "validate", "--inventory", fixture)
                self.assertNotEqual(result.returncode, 0, f"case {index} unexpectedly passed")
                self.assertIn(message, result.stderr)

    def test_validate_rejects_conflicting_canonical_and_legacy_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            cases = []

            bank = inventory()
            bank["banks"][0]["profile"] = "different"
            cases.append(bank)

            bank_class = inventory()
            bank_class["banks"][0]["kind"] = "personal"
            cases.append(bank_class)

            profile_roles = inventory()
            profile_roles["profiles"][0]["provider_roles"] = {}
            cases.append(profile_roles)

            migration = inventory()
            migration["migration"]["artifact_path"] = "/tmp/different"
            cases.append(migration)

            harness = inventory()
            harness["harnesses"][0]["home_bank"]["profile"] = "different"
            cases.append(harness)

            harness_write = inventory()
            harness_write["harnesses"][0]["bank"] = {
                "profile_id": "core", "bank_id": "different"
            }
            cases.append(harness_write)

            for index, value in enumerate(cases):
                fixture = tmp / f"alias-{index}.json"
                self.write_json(fixture, value)
                result = self.run_cli(tmp, "validate", "--inventory", fixture)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("conflicting", result.stderr)

    def test_inventory_alias_references_reject_non_strings_as_inventory_errors(self):
        for surface, mutate in (
            ("bank", lambda value: value["banks"][0].update(profile_id=[])),
            ("harness", lambda value: value["harnesses"][0].update(profile_id=[])),
        ):
            with self.subTest(surface=surface), tempfile.TemporaryDirectory() as directory:
                value = inventory()
                mutate(value)
                path = Path(directory) / "inventory.json"
                self.write_json(path, value)
                with self.assertRaises(InventoryError):
                    load_inventory(path)

    def test_inventory_rejects_non_string_enums_as_inventory_errors(self):
        mutations = (
            lambda value: value["providers"][0].update(role=[]),
            lambda value: value["providers"][0].update(placement={}),
            lambda value: value["banks"][0].update(data_class=[]),
            lambda value: value["profiles"][0].update(scheme={}),
            lambda value: value["banks"][0].update(authority=[]),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            for index, mutate in enumerate(mutations):
                with self.subTest(index=index):
                    value = inventory()
                    mutate(value)
                    self.write_json(path, value)
                    with self.assertRaises(InventoryError):
                        load_inventory(path)

    def test_validate_accepts_an_approved_https_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            value = inventory()
            value["profiles"][0].update({
                "scheme": "https",
                "host": "Hindsight.Private.Invalid.",
                "port": 443,
                "tenant": "private-tenant",
            })
            value["policy"]["approved_tls_endpoints"] = [{
                "profile_id": "core",
                "scheme": "https",
                "host": "hindsight.private.invalid",
                "port": 443,
                "tenant": "private-tenant",
            }]
            fixture = tmp / "https-profile.json"
            self.write_json(fixture, value)
            result = self.run_cli(tmp, "validate", "--inventory", fixture)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_validate_rejects_https_profile_without_exact_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            value = inventory()
            value["profiles"][0].update({
                "scheme": "https",
                "host": "hindsight.private.invalid",
                "port": 443,
                "tenant": "private-tenant",
            })
            fixture = tmp / "https-profile.json"
            self.write_json(fixture, value)
            result = self.run_cli(tmp, "validate", "--inventory", fixture)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "approved TLS endpoints must exactly match enabled HTTPS profiles",
                result.stderr,
            )

            value["policy"]["approved_tls_endpoints"] = [{
                "profile_id": "core",
                "scheme": "https",
                "host": "other.private.invalid",
                "port": 443,
                "tenant": "private-tenant",
            }]
            self.write_json(fixture, value)
            result = self.run_cli(tmp, "validate", "--inventory", fixture)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "approved TLS endpoints must exactly match enabled HTTPS profiles",
                result.stderr,
            )

    def test_validate_rejects_invalid_unreferenced_providers(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            invalid_providers = [
                {"id": "unused", "role": "secret", "placement": "local", "data_classes": ["engineering"]},
                {"id": "unused", "role": "llm", "placement": "elsewhere", "data_classes": ["engineering"]},
                {"id": "unused", "role": "llm", "placement": "local", "data_classes": "engineering"},
                {"id": "unused", "role": "llm", "placement": "local", "data_classes": ["engineering", 7]},
                {"id": "unused", "role": "llm", "placement": "local", "data_classes": ["unsupported"]},
            ]
            for index, provider in enumerate(invalid_providers):
                value = inventory()
                value["providers"].append(provider)
                fixture = tmp / f"provider-{index}.json"
                self.write_json(fixture, value)
                result = self.run_cli(tmp, "validate", "--inventory", fixture)
                self.assertNotEqual(result.returncode, 0, f"unreferenced provider case {index} unexpectedly passed")
                self.assertIn("provider unused", result.stderr)

    def test_validate_requires_boolean_bank_writable(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            for index, writable in enumerate(("false", 1, 0, None, [], {})):
                value = inventory()
                value["banks"][0]["writable"] = writable
                fixture = tmp / f"writable-{index}.json"
                self.write_json(fixture, value)
                result = self.run_cli(tmp, "validate", "--inventory", fixture)
                self.assertNotEqual(result.returncode, 0, f"non-boolean writable case {index} unexpectedly passed")
                self.assertIn("writable must be boolean", result.stderr)

    def test_plan_is_digest_bound_canonical_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            fixture = tmp / "inventory.json"
            live = tmp / "live.json"
            operations = tmp / "operations.json"
            output = tmp / "plan.json"
            self.write_json(fixture, inventory())
            self.write_json(
                live,
                {
                    "profile_id": "core",
                    "endpoint": {
                        "profile_id": "core",
                        "scheme": "http",
                        "host": "127.0.0.1",
                        "port": 7979,
                        "tenant": "default",
                    },
                    "state": {"banks": []},
                    "compatibility": [{"check": "provider-contract", "compatible": True}],
                },
            )
            self.write_json(operations, {"idle": True, "active": []})

            result = self.run_cli(
                tmp,
                "plan",
                "--inventory", fixture,
                "--live-state", live,
                "--operations", operations,
                "--output", output,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                set(value),
                {
                    "schema_version", "inventory_digest", "artifact_digest",
                    "target_profile", "target_endpoint", "live_state_digest",
                    "operations", "compatibility", "actions", "destructive",
                    "plan_digest",
                },
            )
            self.assertEqual(value["target_endpoint"]["port"], 7979)
            self.assertEqual(value["operations"], {"active": [], "idle": True})
            self.assertEqual(
                [a["id"] for a in value["actions"]],
                [
                    "01-configure-profile-core",
                    "02-create-bank-engineering",
                    "03-configure-bank-engineering",
                    "04-set-auto-consolidation-engineering",
                    "05-set-memory-defense-engineering",
                ],
            )
            resolved = _resolved_artifact(
                load_inventory(fixture).body(),
                base_port=7979,
                machine_engineering_enabled=False,
            )
            self.assertEqual(
                value["actions"][2]["artifact_digest"],
                digest(resolved["banks"][0]),
            )
            self.assertFalse(value["destructive"])
            body = dict(value)
            plan_digest = body.pop("plan_digest")
            canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            self.assertEqual(plan_digest, hashlib.sha256(canonical).hexdigest())
            self.assertEqual(output.read_bytes(), json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n")
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)

            status = self.run_cli(
                tmp,
                "status",
                "--inventory", fixture,
                "--live-state", live,
                "--plan", output,
            )
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(
                {key: json.loads(status.stdout)[key] for key in ("desired_agrees", "live_agrees", "plan_agrees")},
                {"desired_agrees": True, "live_agrees": True, "plan_agrees": True},
            )

    def test_plan_rejects_caller_supplied_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            fixture = tmp / "inventory.json"
            operations = tmp / "operations.json"
            self.write_json(fixture, inventory())
            self.write_json(operations, {"idle": True, "active": []})
            for suffix in ({}, {"destructive": False}):
                live = tmp / f"live-{len(suffix)}.json"
                self.write_json(
                    live,
                    {
                        "profile_id": "core",
                        "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"},
                        "state": {},
                        "compatibility": [],
                        "actions": [{"id": "delete-1", "kind": "delete_bank", "bank": {"profile_id": "core", "bank_id": "engineering"}, **suffix}],
                    },
                )
                result = self.run_cli(tmp, "plan", "--inventory", fixture, "--live-state", live, "--operations", operations)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("cannot supply proposed actions", result.stderr)

    def test_plan_derives_semantic_actions_from_desired_and_observed_state(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            fixture = tmp / "inventory.json"
            value = inventory()
            value["banks"][0].update(
                {
                    "enable_auto_consolidation": False,
                    "memory_defense": "sensitive_data",
                    "models": [{"id": "summary", "revision": "v2"}],
                    "directives": [{"id": "grounded", "text": "Use live truth."}],
                }
            )
            self.write_json(fixture, value)
            desired = load_inventory(fixture)
            resolved = _resolved_artifact(
                desired.body(),
                base_port=7979,
                machine_engineering_enabled=False,
            )
            resolved_profile = resolved["profiles"][0]
            resolved_bank = resolved["banks"][0]
            live = {
                "profile_id": "core",
                "endpoint": {
                    "profile_id": "core", "scheme": "http",
                    "host": "127.0.0.1", "port": 7979, "tenant": "default",
                },
                "state": {
                    "profile_artifact_digest": digest(resolved_profile),
                    "banks": [
                        {
                            "id": "engineering",
                            "artifact_digest": "0" * 64,
                            "enable_auto_consolidation": True,
                            "memory_defense": "disabled",
                            "models": [{"id": "summary", "revision": "v1", "artifact_digest": "1" * 64}],
                            "directives": [],
                        },
                        {"id": "unmanaged"},
                    ]
                },
                "compatibility": [],
            }
            plan = build_plan(desired, live, {"idle": True, "active": []})
            self.assertEqual(
                [action.kind for action in plan.actions],
                [
                    "configure_bank", "set_auto_consolidation",
                    "set_memory_defense", "upsert_model",
                    "upsert_directive", "report_unmanaged",
                ],
            )
            for action in plan.actions:
                if "bank" in action.details:
                    self.assertEqual(action.details["bank"]["profile_id"], "core")
                else:
                    self.assertEqual(action.details["profile_id"], "core")

            bank = value["banks"][0]
            matching = {
                **live,
                "state": {
                    "profile_artifact_digest": digest(resolved_profile),
                    "banks": [
                        {
                            "id": "engineering",
                            "artifact_digest": digest(resolved_bank),
                            "enable_auto_consolidation": False,
                            "memory_defense": "sensitive_data",
                            "models": [
                                {
                                    "id": "summary", "revision": "v2",
                                    "artifact_digest": digest(resolved_bank["models"][0]),
                                }
                            ],
                            "directives": [
                                {
                                    "id": "grounded",
                                    "artifact_digest": digest(resolved_bank["directives"][0]),
                                }
                            ],
                        }
                    ]
                },
            }
            self.assertEqual(
                build_plan(desired, matching, {"idle": True, "active": []}).actions,
                (),
            )

            del matching["state"]["banks"][0]["memory_defense"]
            implicit_default = build_plan(
                desired, matching, {"idle": True, "active": []}
            )
            self.assertEqual(
                [action.kind for action in implicit_default.actions],
                ["set_memory_defense"],
            )
            self.assertEqual(
                implicit_default.actions[0].details["enabled"],
                True,
            )

    def test_inventory_requires_sensitive_data_memory_defense_and_boolean_auto_consolidation(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "inventory.json"
            for key, value in (
                ("memory_defense", True),
                ("memory_defense", "disabled"),
                ("enable_auto_consolidation", "true"),
            ):
                with self.subTest(key=key, value=value):
                    raw = inventory()
                    raw["banks"][0][key] = value
                    self.write_json(fixture, raw)
                    with self.assertRaises(InventoryError):
                        load_inventory(fixture)

            raw = inventory()
            raw["banks"][0]["memory_defense"] = "sensitive_data"
            raw["banks"][0]["enable_auto_consolidation"] = False
            self.write_json(fixture, raw)
            load_inventory(fixture)

    def test_plan_reports_unmanaged_models_and_directives_deterministically(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "inventory.json"
            value = inventory()
            value["banks"][0]["models"] = [
                {"id": "managed-model", "revision": "v1"}
            ]
            value["banks"][0]["directives"] = [
                {"id": "managed-directive", "text": "Stay grounded."}
            ]
            self.write_json(fixture, value)
            desired = load_inventory(fixture)

        live = {
            "profile_id": "core",
            "endpoint": {
                "profile_id": "core",
                "scheme": "http",
                "host": "127.0.0.1",
                "port": 7979,
                "tenant": "default",
            },
            "state": {
                "profile_artifact_digest": digest(value["profiles"][0]),
                "banks": [
                    {
                        "id": "engineering",
                        "artifact_digest": digest(
                            value["banks"][0].get(
                                "config", value["banks"][0]
                            )
                        ),
                        "models": [
                            {
                                "id": "z-unmanaged-model",
                                "revision": "v1",
                                "artifact_digest": "0" * 64,
                            },
                            {
                                "id": "a-unmanaged-model",
                                "revision": "v1",
                                "artifact_digest": "1" * 64,
                            },
                        ],
                        "directives": [
                            {
                                "id": "z-unmanaged-directive",
                                "artifact_digest": "2" * 64,
                            },
                            {
                                "id": "a-unmanaged-directive",
                                "artifact_digest": "3" * 64,
                            },
                        ],
                    }
                ],
            },
            "compatibility": [],
        }

        first = build_plan(desired, live, {"idle": True, "active": []})
        second = build_plan(desired, live, {"idle": True, "active": []})
        reasons = [
            action.details["reason_code"]
            for action in first.actions
            if action.kind == "report_unmanaged"
        ]
        self.assertEqual(
            reasons,
            [
                "unmanaged-model-engineering-a-unmanaged-model",
                "unmanaged-model-engineering-z-unmanaged-model",
                "unmanaged-directive-engineering-a-unmanaged-directive",
                "unmanaged-directive-engineering-z-unmanaged-directive",
            ],
        )
        self.assertEqual(
            [action.id for action in first.actions],
            [action.id for action in second.actions],
        )

    def test_plan_rejects_conflicting_live_bank_id_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "inventory.json"
            self.write_json(fixture, inventory())
            desired = load_inventory(fixture)

        with self.assertRaisesRegex(PlanError, "conflicting bank identities"):
            build_plan(
                desired,
                {
                    "profile_id": "core",
                    "endpoint": {
                        "profile_id": "core",
                        "scheme": "http",
                        "host": "127.0.0.1",
                        "port": 7979,
                        "tenant": "default",
                    },
                    "state": {
                        "banks": [
                            {
                                "id": "engineering",
                                "bank_id": "personal",
                            }
                        ]
                    },
                    "compatibility": [],
                },
                {"idle": True, "active": []},
            )

    def test_plan_rejects_every_embedded_profile_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "inventory.json"
            self.write_json(fixture, inventory())
            desired = load_inventory(fixture)
        endpoint = {
            "profile_id": "core", "scheme": "http",
            "host": "127.0.0.1", "port": 7979, "tenant": "default",
        }
        live = {
            "profile_id": "core",
            "endpoint": endpoint,
            "state": {"banks": []},
            "compatibility": [],
        }
        mismatches = (
            {"idle": False, "active": [{
                "id": "op", "kind": "retain", "status": "running",
                "profile_id": "other",
            }]},
            {"idle": False, "active": [{
                "id": "op", "kind": "retain", "status": "running",
                "bank": {"profile_id": "other", "bank_id": "engineering"},
            }]},
            {"idle": False, "active": [{
                "id": "op", "kind": "retain", "status": "running",
                "endpoint": {**endpoint, "profile_id": "other"},
            }]},
        )
        for operations in mismatches:
            with self.subTest(operations=operations):
                with self.assertRaisesRegex(PlanError, "target profile"):
                    build_plan(desired, live, operations)

        with self.assertRaisesRegex(PlanError, "target profile"):
            build_plan(
                desired,
                {
                    **live,
                    "compatibility": [{
                        "check": "provider", "compatible": True,
                        "profile_id": "other",
                    }],
                },
                {"idle": True, "active": []},
            )

        with self.assertRaisesRegex(PlanError, "profile identities"):
            build_plan(
                desired,
                {**live, "target_profile": "other"},
                {"idle": True, "active": []},
            )
        with self.assertRaisesRegex(PlanError, "endpoint identity"):
            build_plan(
                desired,
                {
                    **live,
                    "target_endpoint": {**endpoint, "tenant": "other"},
                },
                {"idle": True, "active": []},
            )

        with self.assertRaisesRegex(PlanError, "target profile"):
            build_plan(
                desired,
                {**live, "state": {"banks": [{
                    "id": "engineering", "profile_id": "other",
                }]}},
                {"idle": True, "active": []},
            )

    def test_generated_action_and_reason_ids_are_bounded_and_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "inventory.json"
            value = inventory()
            desired_bank_id = "b" * 128
            desired_model_id = "m" * 128
            unmanaged_bank_id = "u" * 128
            value["banks"][0]["id"] = desired_bank_id
            value["harnesses"][0]["home_bank"]["bank_id"] = desired_bank_id
            value["harnesses"][0]["write_bank"]["bank_id"] = desired_bank_id
            value["banks"][0]["models"] = [
                {"id": desired_model_id, "revision": "v1"}
            ]
            self.write_json(fixture, value)
            desired = load_inventory(fixture)
            live = {
                "profile_id": "core",
                "endpoint": {
                    "profile_id": "core",
                    "scheme": "http",
                    "host": "127.0.0.1",
                    "port": 7979,
                    "tenant": "default",
                },
                "state": {"banks": [{"id": unmanaged_bank_id}]},
                "compatibility": [],
            }

            first = build_plan(
                desired, live, {"idle": True, "active": []}
            )
            second = build_plan(
                desired, live, {"idle": True, "active": []}
            )

            self.assertEqual(
                [action.id for action in first.actions],
                [action.id for action in second.actions],
            )
            self.assertTrue(
                all(len(action.id) <= 128 for action in first.actions)
            )
            unmanaged = next(
                action
                for action in first.actions
                if action.kind == "report_unmanaged"
            )
            self.assertLessEqual(len(unmanaged.details["reason_code"]), 128)
            self.assertEqual(
                unmanaged.details["profile_id"], "core"
            )

    def test_plan_artifacts_reject_private_and_payload_carriers(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            fixture = tmp / "inventory.json"
            live = tmp / "live.json"
            operations = tmp / "operations.json"
            self.write_json(fixture, inventory())
            base_live = {
                "profile_id": "core",
                "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"},
                "state": {"banks": []},
                "compatibility": [{"check": "provider-contract", "compatible": True}],
            }
            adversarial = [
                ({"idle": False, "active": [{"id": "op-1", "kind": "retain", "status": "running", "token": "private"}]}, base_live, "operations"),
                ({"idle": True, "active": []}, {**base_live, "compatibility": [{"check": "provider-contract", "compatible": True, "api_key": "private"}]}, "compatibility"),
                ({"idle": True, "active": []}, {**base_live, "actions": [{"id": "create-1", "kind": "create_bank", "control_key": "private"}]}, "cannot supply proposed actions"),
                ({"idle": True, "active": []}, {**base_live, "actions": [{"id": "create-1", "kind": "create_bank", "metadata": {"note": "innocuous nested payload"}}]}, "cannot supply proposed actions"),
            ]
            for index, (operation_value, live_value, message) in enumerate(adversarial):
                self.write_json(operations, operation_value)
                self.write_json(live, live_value)
                result = self.run_cli(tmp, "plan", "--inventory", fixture, "--live-state", live, "--operations", operations)
                self.assertNotEqual(result.returncode, 0, f"adversarial case {index} unexpectedly passed")
                self.assertIn(message, result.stderr)

            desired = load_inventory(fixture)
            with self.assertRaisesRegex(PlanError, "operations entry"):
                build_plan(
                    desired,
                    base_live,
                    OperationSnapshot(False, ({"id": "op-1", "kind": "retain", "status": "running", "signing_key": "private"},)),
                )

    def test_plan_owns_immutable_copies_of_all_nested_values(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            fixture = tmp / "inventory.json"
            self.write_json(fixture, inventory())
            desired = load_inventory(fixture)
            live = {
                "profile_id": "core",
                "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"},
                "state": {"banks": []},
                "compatibility": [{"check": "provider-contract", "compatible": True}],
            }
            operations = {"idle": False, "active": [{"id": "op-1", "kind": "retain", "status": "running", "profile_id": "core"}]}
            plan = build_plan(desired, live, operations)
            before = canonical_bytes(plan.to_dict())

            live["compatibility"][0]["compatible"] = False
            live["state"]["banks"].append({"id": "personal"})
            operations["active"][0]["status"] = "failed"
            with self.assertRaises(TypeError):
                plan.compatibility[0]["compatible"] = False
            with self.assertRaises(TypeError):
                plan.actions[0].details["bank"] = {"bank_id": "personal"}
            with self.assertRaises(TypeError):
                plan.operations.active[0]["status"] = "failed"
            with self.assertRaises(TypeError):
                desired.profiles[0]["id"] = "changed"

            self.assertEqual(canonical_bytes(plan.to_dict()), before)
            verify_plan(plan)

    def test_plan_rejects_duplicate_active_operations_and_compatibility_contradictions(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "inventory.json"
            self.write_json(fixture, inventory())
            desired = load_inventory(fixture)
        live = {
            "profile_id": "core",
            "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"},
            "state": {"banks": []},
            "compatibility": [],
        }
        duplicate = {"id": "same", "kind": "retain", "status": "running"}
        with self.assertRaisesRegex(PlanError, "IDs must be unique"):
            build_plan(
                desired,
                live,
                {"idle": False, "active": [duplicate, dict(duplicate)]},
            )
        for compatible, status in ((True, "fail"), (False, "pass")):
            with self.subTest(compatible=compatible, status=status):
                contradicted = {
                    **live,
                    "compatibility": [{
                        "check": "provider-contract",
                        "compatible": compatible,
                        "status": status,
                    }],
                }
                with self.assertRaisesRegex(PlanError, "contradicts"):
                    build_plan(
                        desired,
                        contradicted,
                        {"idle": True, "active": []},
                    )

    def test_plan_rejects_non_string_enums_as_plan_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "inventory.json"
            self.write_json(fixture, inventory())
            desired = load_inventory(fixture)
        live = {
            "profile_id": "core",
            "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"},
            "state": {"banks": []},
            "compatibility": [],
        }
        cases = (
            ({"idle": False, "active": [{"id": "op", "kind": [], "status": "running"}]}, live),
            ({"idle": False, "active": [{"id": "op", "kind": "retain", "status": {}}]}, live),
            ({"idle": True, "active": []}, {**live, "compatibility": [{"check": "provider-contract", "compatible": True, "status": []}]}),
        )
        for operations, live_state in cases:
            with self.subTest(operations=operations, live_state=live_state):
                with self.assertRaises(PlanError):
                    build_plan(desired, live_state, operations)
        malformed_action_plan = build_plan(
            desired, live, {"idle": True, "active": []}
        ).to_dict()
        malformed_action_plan["actions"] = [{"id": "action", "kind": {}}]
        with self.assertRaises(PlanError):
            plan_from_dict(malformed_action_plan)

    def test_plan_rejects_live_endpoint_drift_from_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            fixture = tmp / "inventory.json"
            live = tmp / "live.json"
            operations = tmp / "operations.json"
            self.write_json(fixture, inventory())
            self.write_json(operations, {"idle": True, "active": []})
            self.write_json(live, {"profile_id": "core", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7980, "tenant": "default"}, "state": {"banks": []}, "compatibility": []})
            result = self.run_cli(tmp, "plan", "--inventory", fixture, "--live-state", live, "--operations", operations)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("endpoint identity does not match inventory", result.stderr)

    def test_status_rejects_unknown_and_malformed_plan_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            fixture = tmp / "inventory.json"
            live = tmp / "live.json"
            operations = tmp / "operations.json"
            plan = tmp / "plan.json"
            self.write_json(fixture, inventory())
            self.write_json(operations, {"idle": True, "active": []})
            self.write_json(live, {"profile_id": "core", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}, "state": {"banks": []}, "compatibility": []})
            self.assertEqual(self.run_cli(tmp, "plan", "--inventory", fixture, "--live-state", live, "--operations", operations, "--output", plan).returncode, 0)

            value = json.loads(plan.read_text())
            value["secret"] = "private"
            self.write_json(plan, value)
            unknown = self.run_cli(tmp, "status", "--inventory", fixture, "--live-state", live, "--plan", plan)
            self.assertNotEqual(unknown.returncode, 0)
            self.assertIn("plan keys", unknown.stderr)

            del value["secret"]
            value["plan_digest"] = "not-a-digest"
            self.write_json(plan, value)
            malformed = self.run_cli(tmp, "status", "--inventory", fixture, "--live-state", live, "--plan", plan)
            self.assertNotEqual(malformed.returncode, 0)
            self.assertIn("plan_digest", malformed.stderr)

            value["plan_digest"] = "0" * 64
            self.write_json(plan, value)
            tampered = self.run_cli(tmp, "status", "--inventory", fixture, "--live-state", live, "--plan", plan)
            self.assertNotEqual(tampered.returncode, 0)
            self.assertIn("plan digest does not match plan body", tampered.stderr)

    def test_ledger_is_canonical_private_and_payload_free(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "controller.jsonl"
            record = {
                "schema_version": 1,
                "action_id": "retain-1",
                "correlation_id": "session-1",
                "source_bank": {"profile_id": "core", "bank_id": "engineering", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
                "target_bank": {"profile_id": "core", "bank_id": "personal", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
                "policy_digest": "1" * 64,
                "artifact_digest": "2" * 64,
                "decision": "deny",
                "reason_code": "CROSS_BANK_POLICY_DENY",
                "timestamp": "2026-07-12T17:00:00Z",
                "reversible_record_id": None,
            }
            append_record(ledger, record)
            self.assertEqual(ledger.read_bytes(), json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n")
            self.assertEqual(os.stat(ledger).st_mode & 0o777, 0o600)

            relative_ledger = Path(directory) / "relative-controller.jsonl"
            append_record(os.path.relpath(relative_ledger), record)
            self.assertEqual(
                relative_ledger.read_bytes(), canonical_bytes(record) + b"\n"
            )

            unsafe_parent = Path(directory) / "unsafe-ledger-parent"
            unsafe_parent.mkdir(mode=0o700)
            unsafe_parent.chmod(0o733)
            try:
                with self.assertRaisesRegex(OSError, "group or world writable"):
                    append_record(unsafe_parent / "controller.jsonl", record)
                self.assertFalse((unsafe_parent / "controller.jsonl").exists())
            finally:
                unsafe_parent.chmod(0o700)

            oversized_ledger = Path(directory) / "oversized-ledger.jsonl"
            oversized_ledger.write_bytes(
                b"x" * (MAX_LEDGER_RECORD_BYTES + 1) + b"\n"
            )
            oversized_ledger.chmod(0o600)
            with self.assertRaisesRegex(LedgerError, "existing ledger record"):
                append_record_once(oversized_ledger, record)

            malformed_history = Path(directory) / "malformed-history.jsonl"
            malformed_history.write_text(
                json.dumps({**record, "action_id": "different", "unknown": True})
                + "\n",
                encoding="utf-8",
            )
            malformed_history.chmod(0o600)
            malformed_before = malformed_history.read_bytes()
            with self.assertRaisesRegex(LedgerError, "existing ledger record"):
                append_record(malformed_history, record)
            self.assertEqual(malformed_history.read_bytes(), malformed_before)

            loose_ledger = Path(directory) / "loose-ledger.jsonl"
            loose_ledger.write_bytes(b"")
            loose_ledger.chmod(0o644)
            with self.assertRaisesRegex(OSError, "permissions are unsafe"):
                append_record_once(loose_ledger, record)
            self.assertEqual(stat.S_IMODE(loose_ledger.stat().st_mode), 0o644)

            sticky_parent = Path(directory) / "sticky-ledger-parent"
            sticky_parent.mkdir(mode=0o700)
            sticky_parent.chmod(0o1777)
            sticky_child = sticky_parent / "private"
            sticky_child.mkdir(mode=0o700)
            append_record(sticky_child / "controller.jsonl", record)

            real_fstat = os.fstat

            def foreign_file_owner(descriptor):
                metadata = real_fstat(descriptor)
                if stat.S_ISREG(metadata.st_mode):
                    values = list(metadata)
                    values[4] = os.geteuid() + 1
                    return os.stat_result(values)
                return metadata

            with patch(
                "hindsight_memory_control_plane.ledger.os.fstat",
                side_effect=foreign_file_owner,
            ):
                with self.assertRaisesRegex(OSError, "owned by the current user"):
                    append_record(ledger, record)

            before = ledger.read_bytes()
            real_write = os.write
            writes = 0

            def partial_then_fail(descriptor, body):
                nonlocal writes
                writes += 1
                if writes == 1:
                    chunk = body[:max(1, len(body) // 2)]
                    return real_write(descriptor, chunk)
                raise OSError("append failed")

            with patch(
                "hindsight_memory_control_plane.ledger.os.write",
                side_effect=partial_then_fail,
            ):
                with self.assertRaisesRegex(OSError, "append failed"):
                    append_record(ledger, record)
            self.assertEqual(ledger.read_bytes(), before)

            protected = Path(directory) / "protected.jsonl"
            protected.write_text("preserve", encoding="utf-8")
            linked_ledger = Path(directory) / "linked.jsonl"
            linked_ledger.symlink_to(protected)
            with self.assertRaises(OSError):
                append_record(linked_ledger, record)
            self.assertEqual(protected.read_text(encoding="utf-8"), "preserve")

            real_parent = Path(directory) / "real-ledger-parent"
            nested_parent = real_parent / "nested"
            nested_parent.mkdir(parents=True)
            linked_parent = Path(directory) / "linked-ledger-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            with self.assertRaises(OSError):
                append_record(linked_parent / "nested" / "controller.jsonl", record)
            self.assertFalse((nested_parent / "controller.jsonl").exists())

            fifo = Path(directory) / "ledger.fifo"
            os.mkfifo(fifo, 0o600)
            with self.assertRaises(OSError):
                append_record(fifo, record)

            hardlink_source = Path(directory) / "hardlink-source.jsonl"
            hardlink_source.write_text("preserve", encoding="utf-8")
            hardlink_ledger = Path(directory) / "hardlink-ledger.jsonl"
            os.link(hardlink_source, hardlink_ledger)
            with self.assertRaises(OSError):
                append_record(hardlink_ledger, record)
            self.assertEqual(hardlink_source.read_text(encoding="utf-8"), "preserve")

            for key in ("token", "api_key", "control_key", "signing_key", "secret"):
                contaminated = dict(record)
                contaminated["source_bank"] = {**record["source_bank"], key: "private"}
                with self.assertRaisesRegex(LedgerError, "bank reference keys"):
                    append_record(ledger, contaminated)

            contaminated = dict(record)
            contaminated["source_bank"] = {**record["source_bank"], "metadata": {"note": "innocuous nested payload"}}
            with self.assertRaisesRegex(LedgerError, "bank reference keys"):
                append_record(ledger, contaminated)

            for missing in ("source_bank", "target_bank", "reversible_record_id"):
                incomplete = dict(record)
                del incomplete[missing]
                with self.assertRaisesRegex(LedgerError, "missing keys"):
                    append_record(ledger, incomplete)

    def test_ledger_append_uses_one_canonical_snapshot_of_mutable_input(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "controller.jsonl"
            record = {
                "schema_version": 1,
                "action_id": "retain-snapshot",
                "correlation_id": "session-snapshot",
                "source_bank": {"profile_id": "core", "bank_id": "engineering", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
                "target_bank": {"profile_id": "core", "bank_id": "personal", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
                "policy_digest": "1" * 64,
                "artifact_digest": "2" * 64,
                "decision": "deny",
                "reason_code": "SNAPSHOT_TEST",
                "timestamp": "2026-07-12T17:00:00Z",
                "reversible_record_id": None,
            }
            expected = canonical_bytes(record) + b"\n"

            def validate_then_mutate(snapshot):
                validate_record(snapshot)
                record["reason_code"] = "MUTATED_AFTER_SNAPSHOT"

            with patch(
                "hindsight_memory_control_plane.ledger.validate_record",
                side_effect=validate_then_mutate,
            ):
                append_record_once(ledger, record)

            self.assertEqual(ledger.read_bytes(), expected)

    def test_ledger_recovers_only_an_unterminated_final_tail(self):
        base = {
            "schema_version": 1,
            "action_id": "retain-prefix",
            "correlation_id": "session-prefix",
            "source_bank": {"profile_id": "core", "bank_id": "engineering", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "target_bank": {"profile_id": "core", "bank_id": "personal", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "policy_digest": "1" * 64,
            "artifact_digest": "2" * 64,
            "decision": "deny",
            "reason_code": "RECOVERY_TEST",
            "timestamp": "2026-07-12T17:00:00Z",
            "reversible_record_id": None,
        }
        appended = {
            **base,
            "action_id": "retain-after-recovery",
            "correlation_id": "session-after-recovery",
        }
        corrupt_tails = (
            b'{"schema_version":1,"action_id":"incomplete"',
            canonical_bytes({**base, "policy_digest": "not-a-checksum"}),
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, tail in enumerate(corrupt_tails):
                with self.subTest(index=index):
                    ledger = Path(directory) / f"controller-{index}.jsonl"
                    append_record(ledger, base)
                    with ledger.open("ab") as stream:
                        stream.write(tail)
                    append_record_once(ledger, appended)
                    self.assertEqual(
                        [json.loads(line)["action_id"] for line in ledger.read_text().splitlines()],
                        ["retain-prefix", "retain-after-recovery"],
                    )

            first_record = Path(directory) / "first-record.jsonl"
            first_record.write_bytes(corrupt_tails[0])
            first_record.chmod(0o600)
            first_record_before = first_record.read_bytes()
            with self.assertRaisesRegex(LedgerError, "existing ledger record"):
                append_record_once(first_record, appended)
            self.assertEqual(first_record.read_bytes(), first_record_before)

            newline_terminated = Path(directory) / "newline-terminated.jsonl"
            append_record(newline_terminated, base)
            malformed = canonical_bytes(
                {**base, "policy_digest": "not-a-checksum"}
            ) + b"\n"
            with newline_terminated.open("ab") as stream:
                stream.write(malformed)
            before = newline_terminated.read_bytes()
            with self.assertRaisesRegex(LedgerError, "existing ledger record"):
                append_record_once(newline_terminated, appended)
            self.assertEqual(newline_terminated.read_bytes(), before)

            idempotent = Path(directory) / "idempotent-recovery.jsonl"
            self.assertTrue(append_record_once(idempotent, base))
            with idempotent.open("ab") as stream:
                stream.write(b'{"schema_version":1')
            self.assertFalse(append_record_once(idempotent, base))
            self.assertFalse(append_record_once(idempotent, base))

    def test_markerless_ordinary_append_initializes_complete_index(self):
        base = {
            "schema_version": 1,
            "action_id": "retain-prefix",
            "correlation_id": "session-prefix",
            "source_bank": {"profile_id": "core", "bank_id": "engineering", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "target_bank": {"profile_id": "core", "bank_id": "personal", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "policy_digest": "1" * 64,
            "artifact_digest": "2" * 64,
            "decision": "deny",
            "reason_code": "BOUNDED_TAIL_TEST",
            "timestamp": "2026-07-12T17:00:00Z",
            "reversible_record_id": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "controller.jsonl"
            ledger.write_bytes(
                b"".join(
                    canonical_bytes(
                        {
                            **base,
                            "action_id": f"retain-{index}",
                            "correlation_id": f"session-{index}",
                        }
                    )
                    + b"\n"
                    for index in range(2000)
                )
            )
            ledger.chmod(0o600)
            append_record(
                ledger,
                {
                    **base,
                    "action_id": "retain-after-history",
                    "correlation_id": "session-after-history",
                },
            )
            index = next(Path(directory).glob(".hindsight-ledger-index-*"))
            marker = json.loads((index / "complete").read_text())
            self.assertEqual(marker["schema_version"], 6)
            self.assertRegex(marker["index_root_digest"], r"^[0-9a-f]{64}$")
            self.assertRegex(marker["ledger_chain_digest"], r"^[0-9a-f]{64}$")
            self.assertEqual(marker["indexed_bytes"], ledger.stat().st_size)
            self.assertEqual(len(ledger.read_text().splitlines()), 2001)

    def test_ledger_invalidates_complete_marker_before_tail_index_mutation(self):
        base = {
            "schema_version": 1,
            "action_id": "retain-index-base",
            "correlation_id": "session-index-base",
            "source_bank": {"profile_id": "core", "bank_id": "engineering", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "target_bank": {"profile_id": "core", "bank_id": "personal", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "policy_digest": "1" * 64,
            "artifact_digest": "2" * 64,
            "decision": "deny",
            "reason_code": "INDEX_INVALIDATION_TEST",
            "timestamp": "2026-07-12T17:00:00Z",
            "reversible_record_id": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "controller.jsonl"
            append_record_once(ledger, base)
            index = next(Path(directory).glob(".hindsight-ledger-index-*"))
            external = {
                **base,
                "action_id": "retain-external",
                "correlation_id": "session-external",
            }
            with ledger.open("ab") as stream:
                stream.write(canonical_bytes(external) + b"\n")

            def fail_after_marker_invalidation(*_args, **_kwargs):
                self.assertFalse((index / "complete").exists())
                raise OSError("injected index write failure")

            with patch.object(
                ledger_module,
                "_put_index_entry",
                side_effect=fail_after_marker_invalidation,
            ), self.assertRaisesRegex(OSError, "injected index write failure"):
                append_record_once(
                    ledger,
                    {
                        **base,
                        "action_id": "retain-next",
                        "correlation_id": "session-next",
                    },
                )
            self.assertFalse((index / "complete").exists())

    def test_ledger_identity_index_preserves_conflict_history(self):
        base = {
            "schema_version": 1,
            "action_id": "retain-indexed",
            "correlation_id": "session-indexed",
            "source_bank": {"profile_id": "core", "bank_id": "engineering", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "target_bank": {"profile_id": "core", "bank_id": "personal", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "policy_digest": "1" * 64,
            "artifact_digest": "2" * 64,
            "decision": "deny",
            "reason_code": "INDEX_TEST",
            "timestamp": "2026-07-12T17:00:00Z",
            "reversible_record_id": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "controller.jsonl"
            append_record(ledger, base)
            self.assertFalse(append_record_once(ledger, base))
            append_record(ledger, {**base, "reason_code": "INDEX_CONFLICT"})
            with self.assertRaisesRegex(LedgerError, "identity conflicts"):
                append_record_once(ledger, base)

    def test_ledger_identity_index_tree_rejects_deleted_or_tampered_entries(self):
        base = {
            "schema_version": 1,
            "action_id": "retain-manifest",
            "correlation_id": "session-manifest",
            "source_bank": {"profile_id": "core", "bank_id": "engineering", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "target_bank": {"profile_id": "core", "bank_id": "personal", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "policy_digest": "1" * 64,
            "artifact_digest": "2" * 64,
            "decision": "deny",
            "reason_code": "INDEX_MANIFEST_TEST",
            "timestamp": "2026-07-12T17:00:00Z",
            "reversible_record_id": None,
        }
        for mutation in ("delete", "tamper"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                ledger = Path(directory) / "controller.jsonl"
                self.assertTrue(append_record_once(ledger, base))
                before = ledger.read_bytes()
                index = next(Path(directory).glob(".hindsight-ledger-index-*"))
                entry = next(index.glob("*.json"))
                if mutation == "delete":
                    entry.unlink()
                else:
                    entry.write_bytes(b"{}")
                    entry.chmod(0o600)

                with self.assertRaisesRegex(LedgerError, "authentication path"):
                    append_record_once(ledger, base)
                self.assertEqual(ledger.read_bytes(), before)

    def test_ledger_identity_index_tree_rejects_tampered_authentication_node(self):
        base = {
            "schema_version": 1,
            "action_id": "retain-auth-node",
            "correlation_id": "session-auth-node",
            "source_bank": {"profile_id": "core", "bank_id": "engineering", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "target_bank": {"profile_id": "core", "bank_id": "personal", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "policy_digest": "1" * 64,
            "artifact_digest": "2" * 64,
            "decision": "deny",
            "reason_code": "INDEX_AUTH_NODE_TEST",
            "timestamp": "2026-07-12T17:00:00Z",
            "reversible_record_id": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "controller.jsonl"
            append_record_once(ledger, base)
            index = next(Path(directory).glob(".hindsight-ledger-index-*"))
            marker = json.loads((index / "complete").read_text())
            (index / f'.auth-{marker["index_root_key"]}').write_bytes(b"{}")
            with self.assertRaisesRegex(LedgerError, "authentication path"):
                append_record_once(ledger, base)

    def test_markerless_index_rebuild_recovers_authenticated_tree(self):
        base = {
            "schema_version": 1,
            "action_id": "retain-tree-rebuild",
            "correlation_id": "session-tree-rebuild",
            "source_bank": {"profile_id": "core", "bank_id": "engineering", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "target_bank": {"profile_id": "core", "bank_id": "personal", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "policy_digest": "1" * 64,
            "artifact_digest": "2" * 64,
            "decision": "deny",
            "reason_code": "INDEX_REBUILD_TEST",
            "timestamp": "2026-07-12T17:00:00Z",
            "reversible_record_id": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "controller.jsonl"
            append_record_once(ledger, base)
            index = next(Path(directory).glob(".hindsight-ledger-index-*"))
            next(index.glob("*.json")).write_bytes(b"{}")
            (index / "complete").unlink()

            self.assertFalse(append_record_once(ledger, base))
            marker = json.loads((index / "complete").read_text())
            self.assertEqual(marker["schema_version"], 6)

    def test_ledger_identity_index_rejects_schema_two_marker(self):
        record = {
            "schema_version": 1,
            "action_id": "retain-legacy-index",
            "correlation_id": "session-legacy-index",
            "source_bank": {"profile_id": "core", "bank_id": "engineering", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "target_bank": {"profile_id": "core", "bank_id": "personal", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "policy_digest": "1" * 64,
            "artifact_digest": "2" * 64,
            "decision": "deny",
            "reason_code": "LEGACY_INDEX_TEST",
            "timestamp": "2026-07-12T17:00:00Z",
            "reversible_record_id": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "controller.jsonl"
            append_record_once(ledger, record)
            index = next(Path(directory).glob(".hindsight-ledger-index-*"))
            marker_path = index / "complete"
            marker = json.loads(marker_path.read_text())
            marker["schema_version"] = 2
            marker["tail_digest"] = hashlib.sha256(ledger.read_bytes()).hexdigest()
            del marker["ledger_chain_digest"]
            del marker["index_root_key"]
            del marker["index_root_digest"]
            del marker["ledger_mtime_ns"]
            del marker["ledger_ctime_ns"]
            marker_path.write_bytes(canonical_bytes(marker))

            before = ledger.read_bytes()
            with self.assertRaisesRegex(LedgerError, "marker is invalid"):
                append_record_once(ledger, record)
            self.assertEqual(ledger.read_bytes(), before)

    def test_legacy_v5_index_migrates_after_full_authentication(self):
        record = {
            "schema_version": 1,
            "action_id": "retain-v5-index",
            "correlation_id": "session-v5-index",
            "source_bank": {"profile_id": "core", "bank_id": "engineering", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "target_bank": {"profile_id": "core", "bank_id": "personal", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "policy_digest": "1" * 64,
            "artifact_digest": "2" * 64,
            "decision": "deny",
            "reason_code": "V5_MIGRATION_TEST",
            "timestamp": "2026-07-12T17:00:00Z",
            "reversible_record_id": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "controller.jsonl"
            append_record_once(ledger, record)
            index = next(Path(directory).glob(".hindsight-ledger-index-*"))
            for node in index.glob(".auth-*"):
                node.unlink()
            marker_path = index / "complete"
            marker = json.loads(marker_path.read_text())
            entries = [
                [entry.name, hashlib.sha256(entry.read_bytes()).hexdigest()]
                for entry in sorted(index.glob("*.json"))
            ]
            marker["schema_version"] = 5
            marker["prefix_digest"] = hashlib.sha256(ledger.read_bytes()).hexdigest()
            marker["index_manifest_digest"] = hashlib.sha256(
                canonical_bytes(entries)
            ).hexdigest()
            del marker["ledger_chain_digest"]
            del marker["index_root_key"]
            del marker["index_root_digest"]
            marker_path.write_bytes(canonical_bytes(marker))

            from hindsight_memory_control_plane import ledger as ledger_module
            events = []
            real_prefix = ledger_module._ledger_prefix_digest
            real_manifest = ledger_module._index_manifest_digest
            with (
                patch(
                    "hindsight_memory_control_plane.ledger._ledger_prefix_digest",
                    side_effect=lambda *args: (
                        events.append("prefix"), real_prefix(*args)
                    )[1],
                ),
                patch(
                    "hindsight_memory_control_plane.ledger._index_manifest_digest",
                    side_effect=lambda *args: (
                        events.append("manifest"), real_manifest(*args)
                    )[1],
                ),
            ):
                self.assertFalse(append_record_once(ledger, record))
            self.assertLess(events.index("prefix"), events.index("manifest"))
            migrated = json.loads(marker_path.read_text())
            self.assertEqual(migrated["schema_version"], 6)
            self.assertRegex(migrated["index_root_digest"], r"^[0-9a-f]{64}$")

    def test_legacy_v3_index_rejects_prefix_rewrite_disguised_as_growth(self):
        base = {
            "schema_version": 1,
            "action_id": "retain-0000",
            "correlation_id": "session-0000",
            "source_bank": {"profile_id": "core", "bank_id": "engineering", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "target_bank": {"profile_id": "core", "bank_id": "personal", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "policy_digest": "1" * 64,
            "artifact_digest": "2" * 64,
            "decision": "deny",
            "reason_code": "LEGACY_GROWTH_TEST",
            "timestamp": "2026-07-12T17:00:00Z",
            "reversible_record_id": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "controller.jsonl"
            ledger.write_bytes(
                b"".join(
                    canonical_bytes(
                        {
                            **base,
                            "action_id": f"retain-{index:04d}",
                            "correlation_id": f"session-{index:04d}",
                        }
                    )
                    + b"\n"
                    for index in range(400)
                )
            )
            ledger.chmod(0o600)
            append_record(
                ledger,
                {
                    **base,
                    "action_id": "retain-indexed",
                    "correlation_id": "session-indexed",
                },
            )
            index = next(Path(directory).glob(".hindsight-ledger-index-*"))
            marker_path = index / "complete"
            marker = json.loads(marker_path.read_text())
            indexed = marker["indexed_bytes"]
            ledger_bytes = ledger.read_bytes()
            tail_length = min(indexed, TAIL_VALIDATION_BYTES)
            marker["schema_version"] = 3
            marker["tail_digest"] = hashlib.sha256(
                ledger_bytes[indexed - tail_length : indexed]
            ).hexdigest()
            del marker["ledger_chain_digest"]
            del marker["index_root_key"]
            del marker["index_root_digest"]
            marker_path.write_bytes(canonical_bytes(marker))

            rewritten = ledger_bytes.replace(
                b"retain-0000", b"retain-x000", 1
            )
            external = canonical_bytes(
                {
                    **base,
                    "action_id": "retain-external",
                    "correlation_id": "session-external",
                }
            ) + b"\n"
            ledger.write_bytes(rewritten + external)
            before = ledger.read_bytes()

            with self.assertRaisesRegex(
                LedgerError, "legacy ledger identity index marker"
            ):
                append_record(
                    ledger,
                    {
                        **base,
                        "action_id": "retain-after-growth",
                        "correlation_id": "session-after-growth",
                    },
                )
            self.assertEqual(ledger.read_bytes(), before)

    def test_v6_marker_rejects_file_changes_during_chain_hashing(self):
        base = {
            "schema_version": 1,
            "action_id": "retain-indexed",
            "correlation_id": "session-indexed",
            "source_bank": {"profile_id": "core", "bank_id": "engineering", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "target_bank": {"profile_id": "core", "bank_id": "personal", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "policy_digest": "1" * 64,
            "artifact_digest": "2" * 64,
            "decision": "deny",
            "reason_code": "MARKER_RACE_TEST",
            "timestamp": "2026-07-12T17:00:00Z",
            "reversible_record_id": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "controller.jsonl"
            append_record(ledger, base)
            index_path = next(
                Path(directory).glob(".hindsight-ledger-index-*")
            )
            marker_path = index_path / "complete"
            marker_before = marker_path.read_bytes()
            descriptor = os.open(ledger, os.O_RDWR | os.O_APPEND)
            index = os.open(index_path, os.O_RDONLY | os.O_DIRECTORY)
            indexed = os.fstat(descriptor).st_size
            original_digest = ledger_module._ledger_chain_digest

            def rewrite_while_hashing(opened, length):
                prefix = os.pread(opened, length, 0).replace(
                    b"retain-indexed", b"retain-rewrote", 1
                )
                os.pwrite(opened, prefix, 0)
                os.write(
                    opened,
                    canonical_bytes(
                        {
                            **base,
                            "action_id": "retain-external",
                            "correlation_id": "session-external",
                        }
                    )
                    + b"\n",
                )
                os.fsync(opened)
                return original_digest(opened, length)

            try:
                with patch.object(
                    ledger_module,
                    "_ledger_chain_digest",
                    side_effect=rewrite_while_hashing,
                ), self.assertRaisesRegex(
                    LedgerError, "changed while authenticating indexed prefix"
                ):
                    ledger_module._write_index_marker(
                        index, descriptor, indexed
                    )
            finally:
                os.close(index)
                os.close(descriptor)

            self.assertEqual(marker_path.read_bytes(), marker_before)
            with self.assertRaisesRegex(
                LedgerError, "marker does not match ledger"
            ):
                append_record(
                    ledger,
                    {
                        **base,
                        "action_id": "retain-after-race",
                        "correlation_id": "session-after-race",
                    },
                )

    def test_ledger_identity_index_rejects_same_name_replacement(self):
        base = {
            "schema_version": 1,
            "action_id": "retain-indexed",
            "correlation_id": "session-indexed",
            "source_bank": {"profile_id": "core", "bank_id": "engineering", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "target_bank": {"profile_id": "core", "bank_id": "personal", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "policy_digest": "1" * 64,
            "artifact_digest": "2" * 64,
            "decision": "deny",
            "reason_code": "INDEX_TEST",
            "timestamp": "2026-07-12T17:00:00Z",
            "reversible_record_id": None,
        }
        replacement = {
            **base,
            "action_id": "retain-rotated",
            "correlation_id": "session-rotated",
        }
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "controller.jsonl"
            append_record_once(ledger, base)
            rotated = Path(directory) / "rotated.jsonl"
            rotated.write_bytes(canonical_bytes(replacement) + b"\n")
            rotated.chmod(0o600)
            os.replace(rotated, ledger)

            with self.assertRaisesRegex(LedgerError, "marker does not match ledger"):
                append_record_once(ledger, base)
            self.assertEqual(
                [record["action_id"] for record in map(json.loads, ledger.read_text().splitlines())],
                ["retain-rotated"],
            )

    def test_ledger_append_rechecks_locked_path_after_publication(self):
        base = {
            "schema_version": 1,
            "action_id": "retain-initial",
            "correlation_id": "session-initial",
            "source_bank": {"profile_id": "core", "bank_id": "engineering", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "target_bank": {"profile_id": "core", "bank_id": "personal", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "policy_digest": "1" * 64,
            "artifact_digest": "2" * 64,
            "decision": "deny",
            "reason_code": "LOCKED_PATH_TEST",
            "timestamp": "2026-07-12T17:00:00Z",
            "reversible_record_id": None,
        }
        replacement_record = {
            **base,
            "action_id": "retain-replacement",
            "correlation_id": "session-replacement",
        }
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "controller.jsonl"
            append_record(ledger, base)
            replacement = Path(directory) / "replacement.jsonl"
            replacement.write_bytes(canonical_bytes(replacement_record) + b"\n")
            replacement.chmod(0o600)
            expected = replacement.read_bytes()
            original_check = ledger_module._require_locked_ledger_path
            checks = 0

            def replace_before_final_check(directory_fd, name, descriptor):
                nonlocal checks
                checks += 1
                if checks == 2:
                    os.replace(replacement, ledger)
                return original_check(directory_fd, name, descriptor)

            with patch.object(
                ledger_module,
                "_require_locked_ledger_path",
                side_effect=replace_before_final_check,
            ), self.assertRaisesRegex(
                LedgerError, "destination changed while locked"
            ):
                append_record(
                    ledger,
                    {
                        **base,
                        "action_id": "retain-appended",
                        "correlation_id": "session-appended",
                    },
                )

            self.assertEqual(checks, 2)
            self.assertEqual(ledger.read_bytes(), expected)

    def test_ledger_marker_rejects_earlier_same_inode_rewrites(self):
        base = {
            "schema_version": 1,
            "action_id": "retain-indexed",
            "correlation_id": "session-indexed",
            "source_bank": {"profile_id": "core", "bank_id": "engineering", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "target_bank": {"profile_id": "core", "bank_id": "personal", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "policy_digest": "1" * 64,
            "artifact_digest": "2" * 64,
            "decision": "deny",
            "reason_code": "INDEX_REWRITE_TEST",
            "timestamp": "2026-07-12T17:00:00Z",
            "reversible_record_id": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "controller.jsonl"
            append_record_once(ledger, base)
            before = ledger.read_bytes()
            rewritten = before.replace(b"retain-indexed", b"retain-rewrote", 1)
            self.assertEqual(len(rewritten), len(before))
            descriptor = os.open(ledger, os.O_WRONLY)
            try:
                os.pwrite(descriptor, rewritten, 0)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

            with self.assertRaisesRegex(
                LedgerError, "marker does not match ledger"
            ):
                append_record_once(
                    ledger,
                    {
                        **base,
                        "action_id": "retain-after-rewrite",
                        "correlation_id": "session-after-rewrite",
                    },
                )

    def test_ledger_marker_authenticates_full_prefix_before_external_growth(self):
        base = {
            "schema_version": 1,
            "action_id": "retain-0000",
            "correlation_id": "session-0000",
            "source_bank": {"profile_id": "core", "bank_id": "engineering", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "target_bank": {"profile_id": "core", "bank_id": "personal", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "policy_digest": "1" * 64,
            "artifact_digest": "2" * 64,
            "decision": "deny",
            "reason_code": "FULL_PREFIX_TEST",
            "timestamp": "2026-07-12T17:00:00Z",
            "reversible_record_id": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "controller.jsonl"
            ledger.write_bytes(
                b"".join(
                    canonical_bytes(
                        {
                            **base,
                            "action_id": f"retain-{index:04d}",
                            "correlation_id": f"session-{index:04d}",
                        }
                    )
                    + b"\n"
                    for index in range(400)
                )
            )
            ledger.chmod(0o600)
            append_record_once(
                ledger,
                {
                    **base,
                    "action_id": "retain-indexed",
                    "correlation_id": "session-indexed",
                },
            )
            indexed = ledger.read_bytes()
            self.assertGreater(len(indexed), TAIL_VALIDATION_BYTES * 2)

            descriptor = os.open(ledger, os.O_WRONLY)
            try:
                first_action = indexed.index(b"retain-0000")
                os.pwrite(descriptor, b"retain-x000", first_action)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            descriptor = os.open(ledger, os.O_WRONLY | os.O_APPEND)
            try:
                os.write(
                    descriptor,
                    canonical_bytes(
                        {
                            **base,
                            "action_id": "retain-external",
                            "correlation_id": "session-external",
                        }
                    )
                    + b"\n",
                )
                os.write(descriptor, b'{"schema_version":1')
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            before_rejected_append = ledger.read_bytes()

            with self.assertRaisesRegex(
                LedgerError, "marker does not match ledger"
            ):
                append_record(
                    ledger,
                    {
                        **base,
                        "action_id": "retain-after-external-growth",
                        "correlation_id": "session-after-external-growth",
                    },
                )
            self.assertEqual(ledger.read_bytes(), before_rejected_append)

    def test_established_ledger_extends_authenticated_index_without_rescan(self):
        base = {
            "schema_version": 1,
            "action_id": "retain-bounded",
            "correlation_id": "session-bounded",
            "source_bank": {"profile_id": "core", "bank_id": "engineering", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "target_bank": {"profile_id": "core", "bank_id": "personal", "endpoint": {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}},
            "policy_digest": "1" * 64,
            "artifact_digest": "2" * 64,
            "decision": "deny",
            "reason_code": "INDEX_BOUNDED_TEST",
            "timestamp": "2026-07-12T17:00:00Z",
            "reversible_record_id": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "controller.jsonl"
            append_record_once(ledger, base)
            with (
                patch(
                    "hindsight_memory_control_plane.ledger._initialize_identity_index",
                    side_effect=AssertionError("established index rescanned history"),
                ),
                patch(
                    "hindsight_memory_control_plane.ledger._ledger_prefix_digest",
                    side_effect=AssertionError("established index rehashed prefix"),
                ),
                patch(
                    "hindsight_memory_control_plane.ledger._ledger_chain_digest",
                    side_effect=AssertionError("established index rehashed chain"),
                ),
                patch(
                    "hindsight_memory_control_plane.ledger._index_manifest_digest",
                    side_effect=AssertionError("established index rescanned manifest"),
                ),
            ):
                self.assertFalse(append_record_once(ledger, base))
                append_record(
                    ledger,
                    {
                        **base,
                        "action_id": "retain-next",
                        "correlation_id": "session-next",
                    },
                )
            marker_path = (
                ledger.parent
                / (
                    ".hindsight-ledger-index-"
                    + hashlib.sha256(ledger.name.encode("utf-8")).hexdigest()
                )
                / "complete"
            )
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            self.assertEqual(marker["schema_version"], 6)
            self.assertEqual(marker["indexed_bytes"], ledger.stat().st_size)
            self.assertRegex(marker["ledger_chain_digest"], r"^[0-9a-f]{64}$")
            self.assertIsNotNone(marker["index_root_key"])
            self.assertRegex(marker["index_root_digest"], r"^[0-9a-f]{64}$")

    def test_inventory_ignores_authoritative_write_banks_on_disabled_profiles(self):
        value = inventory()
        value["profiles"].append(
            {
                **value["profiles"][0],
                "id": "disabled",
                "slot": 1,
                "enabled": False,
            }
        )
        value["banks"].append(
            {
                "id": "engineering-disabled",
                "profile_id": "disabled",
                "data_class": "engineering",
                "authority": "authoritative",
                "writable": True,
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            self.write_json(path, value)
            desired = load_inventory(path)
        self.assertEqual(len(desired.banks), 2)

    def test_inventory_rejects_enabled_engineering_authority_when_disabled(self):
        value = inventory()
        value["policy"]["engineering_memory_enabled"] = False
        value["harnesses"] = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            self.write_json(path, value)
            with self.assertRaisesRegex(
                InventoryError, "disabled by engineering memory policy"
            ):
                load_inventory(path)

    def test_artifact_digest_binds_machine_runtime_policy_inputs(self):
        baseline = inventory()
        baseline["machine"]["engineering_memory_enabled"] = True
        changed_port = inventory()
        changed_port["machine"]["engineering_memory_enabled"] = True
        changed_port["machine"]["base_port"] += 100
        changed_engineering_flag = inventory()
        changed_engineering_flag["machine"][
            "engineering_memory_enabled"
        ] = False
        # Keep the effective policy enabled so this test isolates digest input
        # binding rather than exercising the disabled-authority rejection.
        changed_engineering_flag["policy"][
            "engineering_memory_enabled"
        ] = True
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            loaded = []
            for index, value in enumerate(
                (baseline, changed_port, changed_engineering_flag)
            ):
                path = root / f"inventory-{index}.json"
                self.write_json(path, value)
                loaded.append(load_inventory(path))
        self.assertNotEqual(
            loaded[0].artifact_digest, loaded[1].artifact_digest
        )
        self.assertNotEqual(
            loaded[0].artifact_digest, loaded[2].artifact_digest
        )

    def test_artifact_digest_uses_resolved_canonical_inventory_fields(self):
        concise = inventory()
        explicit = inventory()
        explicit["machine"]["engineering_memory_enabled"] = False
        profile = explicit["profiles"][0]
        profile.pop("slot")
        profile.update({
            "port": 7979,
            "enabled": True,
            "scheme": "http",
            "tenant": "default",
            "provider_roles": profile.pop("roles"),
        })
        bank = explicit["banks"][0]
        bank["profile"] = bank.pop("profile_id")
        bank["kind"] = bank.pop("data_class")
        bank.update({
            "enable_auto_consolidation": False,
            "memory_defense": "sensitive_data",
            "models": [],
            "directives": [],
        })
        harness = explicit["harnesses"][0]
        harness["profile"] = harness.pop("profile_id")
        harness["bank"] = harness.pop("write_bank")
        explicit["policy"]["provider_placements"] = explicit["policy"].pop(
            "allowed_placements"
        )
        explicit["policy"]["approved_tls_endpoints"] = []

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            loaded = []
            for index, value in enumerate((concise, explicit)):
                path = root / f"inventory-{index}.json"
                self.write_json(path, value)
                loaded.append(load_inventory(path))

        self.assertNotEqual(loaded[0].inventory_digest, loaded[1].inventory_digest)
        self.assertEqual(loaded[0].artifact_digest, loaded[1].artifact_digest)

    def test_artifact_digest_canonicalizes_policy_sets_and_tls_endpoints(self):
        canonical = inventory()
        canonical_profile = canonical["profiles"][0]
        canonical_profile.update({
            "scheme": "https",
            "host": "provider.invalid",
            "port": 8443,
        })
        canonical["policy"]["approved_tls_endpoints"] = [{
            "profile_id": "core",
            "scheme": "https",
            "host": "provider.invalid",
            "port": 8443,
            "tenant": "default",
        }]
        alternate = copy.deepcopy(canonical)
        alternate["profiles"][0]["host"] = "Provider.Invalid."
        alternate["policy"]["approved_tls_endpoints"][0]["host"] = (
            "Provider.Invalid."
        )
        for placements in alternate["policy"]["allowed_placements"].values():
            placements.reverse()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            loaded = []
            for index, value in enumerate((canonical, alternate)):
                path = root / f"tls-inventory-{index}.json"
                self.write_json(path, value)
                loaded.append(load_inventory(path))

        self.assertNotEqual(loaded[0].inventory_digest, loaded[1].inventory_digest)
        self.assertEqual(loaded[0].artifact_digest, loaded[1].artifact_digest)

    def test_planning_rejects_inventory_without_canonical_artifact_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            self.write_json(path, inventory())
            desired = load_inventory(path)
        forged = replace(desired, artifact_digest="0" * 64)
        with self.assertRaisesRegex(PlanError, "inventory artifact digest"):
            build_plan(forged, {}, {})


if __name__ == "__main__":
    unittest.main()
