from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from hindsight_memory_control_plane.planning import (
    PlanError,
    _actions,
    _compatibility,
    _endpoint,
    _observed_banks,
    _operations,
    inventory_endpoint,
    plan_from_dict,
)
from hindsight_memory_control_plane.reconcile import _compatibility_identity
from hindsight_memory_control_plane.canonical import digest
from hindsight_memory_control_plane.endpoint_host import canonical_endpoint_host
from hindsight_memory_control_plane.model import EndpointIdentity, Inventory
from hindsight_memory_control_plane.action_contracts import (
    ACTION_SCHEMAS,
    ARTIFACT_ACTION_KINDS,
    DESTRUCTIVE_ACTION_KINDS,
    EXECUTABLE_ACTION_KINDS,
    MUTATION_ACTION_KINDS,
)


class PlanningValidationTest(unittest.TestCase):
    def test_compatibility_identity_excludes_mutable_provider_state(self):
        base = {
            "check": "provider:embed",
            "compatible": False,
            "status": "blocked",
            "provider_state": "blocked_candidate",
            "provider_id": "embed",
        }
        changed = {
            **base,
            "compatible": True,
            "status": "pass",
            "provider_state": "active",
        }
        self.assertEqual(
            _compatibility_identity((base,)),
            _compatibility_identity((changed,)),
        )

    def test_compatibility_normalization_is_order_independent(self):
        first = self.provider_compatibility("z-provider")
        first.update({
            "check": "provider:z-provider",
            "compatible": False,
            "status": "blocked",
            "provider_state": "blocked_candidate",
            "activatable": False,
            "blocked_by": ["z-gate", "a-gate"],
        })
        second = self.provider_compatibility("a-provider")
        left = _compatibility([first, second])
        right = _compatibility(
            [second, {**first, "blocked_by": ["a-gate", "z-gate"]}]
        )
        self.assertEqual(left, right)
        self.assertEqual(left[1]["blocked_by"], ("a-gate", "z-gate"))

    def serialized_plan(self, compatibility):
        body = {
            "schema_version": 1,
            "inventory_digest": "a" * 64,
            "artifact_digest": "b" * 64,
            "target_profile": "core",
            "target_endpoint": {
                "profile_id": "core",
                "scheme": "http",
                "host": "127.0.0.1",
                "port": 7979,
                "tenant": "default",
            },
            "live_state_digest": "c" * 64,
            "operations": {"idle": True, "active": []},
            "compatibility": compatibility,
            "actions": [],
            "destructive": False,
        }
        return {**body, "plan_digest": digest(body)}

    def provider_compatibility(
        self,
        provider_id,
        *,
        role="reranking",
        fallback_provider_id=None,
    ):
        value = {
            "check": f"provider:{provider_id}",
            "provider_id": provider_id,
            "model_id": f"model-{provider_id}",
            "compatible": True,
            "status": "pass",
            "provider_state": "fallback",
            "provider_role": role,
            "activatable": True,
            "blocked_by": [],
            "placement": "local",
            "revision": "rev-1",
        }
        if fallback_provider_id is not None:
            value["fallback_provider_id"] = fallback_provider_id
        return value

    def test_plan_deserialization_validates_provider_fallback_topology(self):
        source = self.provider_compatibility(
            "source", fallback_provider_id="target"
        )
        target = self.provider_compatibility("target")
        try:
            plan_from_dict(self.serialized_plan([source, target]))
        except PlanError as error:
            self.fail(f"valid fallback topology was rejected: {error}")

        cases = (
            (
                [source],
                "fallback target must exist",
            ),
            (
                [
                    self.provider_compatibility(
                        "source", fallback_provider_id="source"
                    )
                ],
                "fallback cannot reference itself",
            ),
            (
                [
                    source,
                    self.provider_compatibility(
                        "target", fallback_provider_id="source"
                    ),
                ],
                "fallback graph contains a cycle",
            ),
            (
                [source, self.provider_compatibility("target", role="llm")],
                "fallback target must support reranking",
            ),
            (
                [
                    self.provider_compatibility(
                        "source", role="llm", fallback_provider_id="target"
                    ),
                    target,
                ],
                "only reranking providers may declare fallbacks",
            ),
            (
                [
                    source,
                    {
                        **target,
                        "provider_state": "current",
                    },
                ],
                "fallback target must be in fallback state",
            ),
        )
        for compatibility, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                PlanError, message
            ):
                plan_from_dict(self.serialized_plan(compatibility))

    def test_compatibility_checks_are_unique_after_format_normalization(self):
        provider_result = {
            "provider_id": "provider-1",
            "role": "embedding",
            "state": "current",
            "compatible": True,
            "activatable": True,
            "blocked_by": [],
            "fallback_provider_id": None,
            "placement": "local",
            "artifact_id": "embedding-model",
            "revision": "rev-1",
        }
        normalized_result = {
            "check": "provider:provider-1",
            "provider_id": "provider-1",
            "model_id": "embedding-model",
            "compatible": True,
            "status": "pass",
            "provider_state": "current",
            "provider_role": "embedding",
            "activatable": True,
            "blocked_by": [],
            "placement": "local",
            "revision": "rev-1",
        }
        with self.assertRaisesRegex(PlanError, "unique after normalization"):
            _compatibility([provider_result, normalized_result])

    def test_generated_provider_check_is_bounded_and_revalidates(self):
        provider_result = {
            "provider_id": "p" * 127,
            "role": "embedding",
            "state": "current",
            "compatible": True,
            "activatable": True,
            "blocked_by": [],
            "fallback_provider_id": None,
            "placement": "local",
            "artifact_id": "embedding-model",
            "revision": "rev-1",
        }
        normalized = _compatibility([provider_result])[0]
        self.assertLessEqual(len(normalized["check"]), 128)
        self.assertEqual(_compatibility([dict(normalized)])[0], normalized)

    def test_endpoint_collision_identity_canonicalizes_dns_and_ip_hosts(self):
        self.assertEqual(
            canonical_endpoint_host("Provider.Invalid."),
            canonical_endpoint_host("provider.invalid"),
        )
        self.assertEqual(
            canonical_endpoint_host("0:0:0:0:0:0:0:1"),
            canonical_endpoint_host("::1"),
        )
        self.assertEqual(
            canonical_endpoint_host("::ffff:c000:0280"),
            "::ffff:192.0.2.128",
        )

    def test_inventory_endpoint_rejects_a_disabled_target_profile(self):
        inventory = Inventory(
            schema_version=1,
            machine={"base_port": 7979},
            archetype={},
            profiles=(
                {
                    "id": "core",
                    "enabled": False,
                    "slot": 0,
                    "scheme": "http",
                    "host": "127.0.0.1",
                    "tenant": "default",
                },
            ),
            providers=(),
            banks=(),
            harnesses=(),
            migration={},
            policy={},
            inventory_digest="a" * 64,
            artifact_digest="b" * 64,
        )
        with self.assertRaisesRegex(PlanError, "target profile is disabled"):
            inventory_endpoint(inventory, "core")

    def test_endpoint_host_is_a_bare_dns_name_or_ip_literal(self):
        for host in ("example.com", "example.com.", "192.0.2.1", "2001:db8::1"):
            with self.subTest(host=host):
                endpoint = _endpoint(
                    {
                        "profile_id": "core",
                        "scheme": "https",
                        "host": host,
                        "port": 443,
                        "tenant": "default",
                    }
                )
                self.assertEqual(
                    endpoint.host, canonical_endpoint_host(host)
                )

        for host in (
            "2130706433", "127.1", "0177.0.0.1", "0x7f000001",
            "0x7f.0.0.1",
        ):
            with self.subTest(host=host), self.assertRaisesRegex(
                PlanError, "bare DNS name or IP literal"
            ):
                _endpoint({
                    "profile_id": "core",
                    "scheme": "https",
                    "host": host,
                    "port": 443,
                    "tenant": "default",
                })

        for host in (
            "user@example.com",
            "example.com:443",
            "example.com/path",
            "example.com?query",
            "example.com#fragment",
            "//example.com",
            "[2001:db8::1]",
            "fe80::1%lo0",
        ):
            with self.subTest(host=host), self.assertRaisesRegex(
                PlanError, "bare DNS name or IP literal"
            ):
                _endpoint(
                    {
                        "profile_id": "core",
                        "scheme": "https",
                        "host": host,
                        "port": 443,
                        "tenant": "default",
                    }
                )

    def test_plain_http_endpoint_remains_literal_loopback_only(self):
        for host in ("127.0.0.1", "::1", "0:0:0:0:0:0:0:1"):
            with self.subTest(host=host):
                self.assertEqual(
                    _endpoint(
                        {
                            "profile_id": "core",
                            "scheme": "http",
                            "host": host,
                            "port": 7979,
                            "tenant": "default",
                        }
                    ).host,
                    canonical_endpoint_host(host),
                )
        with self.assertRaisesRegex(PlanError, "literal loopback"):
            _endpoint(
                {
                    "profile_id": "core",
                    "scheme": "http",
                    "host": "localhost",
                    "port": 7979,
                    "tenant": "default",
                }
            )

    def test_action_catalog_only_advertises_executable_actions(self):
        unsupported = {
            "delete_bank",
            "delete_directive",
            "delete_model",
            "delete_profile",
            "prune_bank",
            "prune_model",
            "retire_artifact",
        }
        self.assertEqual(DESTRUCTIVE_ACTION_KINDS, MUTATION_ACTION_KINDS)
        self.assertEqual(set(ACTION_SCHEMAS), EXECUTABLE_ACTION_KINDS)
        self.assertTrue(unsupported.isdisjoint(ACTION_SCHEMAS))
        self.assertTrue(unsupported.isdisjoint(EXECUTABLE_ACTION_KINDS))
        self.assertIn("activate_model", ARTIFACT_ACTION_KINDS)
        self.assertIn("artifact_digest", ACTION_SCHEMAS["activate_model"])
        self.assertEqual(
            ARTIFACT_ACTION_KINDS,
            {
                kind
                for kind, schema in ACTION_SCHEMAS.items()
                if "artifact_digest" in schema
            },
        )
        migration_fields = {
            "artifact_digest",
            "archive_digest",
            "restore_evidence_digest",
            "source_bank",
            "target_bank",
        }
        for kind in MUTATION_ACTION_KINDS:
            self.assertEqual(ACTION_SCHEMAS[kind], migration_fields)

    def test_activate_model_requires_an_artifact_digest(self):
        action = {
            "id": "activate-model",
            "kind": "activate_model",
            "profile_id": "core",
            "provider_id": "local",
            "model_id": "model-1",
            "revision": "v1",
        }
        with self.assertRaisesRegex(PlanError, "artifact_digest"):
            _actions([action], "core")
        self.assertEqual(
            _actions([{**action, "artifact_digest": "a" * 64}], "core")[0].kind,
            "activate_model",
        )

    def test_active_operation_snapshot_rejects_terminal_statuses(self):
        for status in ("succeeded", "failed", "cancelled"):
            with self.subTest(status=status), self.assertRaisesRegex(
                PlanError, "status must be pending or running"
            ):
                _operations(
                    {
                        "idle": False,
                        "active": [
                            {
                                "id": "operation-1",
                                "kind": "migration",
                                "status": status,
                            }
                        ],
                    }
                )

    def test_active_operation_snapshot_is_canonical_by_operation_id(self):
        operations = [
            {"id": "operation-z", "kind": "retain", "status": "running"},
            {"id": "operation-a", "kind": "reflect", "status": "pending"},
        ]
        first = _operations({"idle": False, "active": operations})
        second = _operations(
            {"idle": False, "active": list(reversed(operations))}
        )

        self.assertEqual(first, second)
        self.assertEqual(
            [operation["id"] for operation in first.active],
            ["operation-a", "operation-z"],
        )

    def test_embedded_live_bank_identity_matches_enclosing_identity(self):
        with self.assertRaisesRegex(
            PlanError,
            "embedded bank identity does not match enclosing bank",
        ):
            _observed_banks(
                {
                    "banks": [
                        {
                            "id": "engineering",
                            "bank": {
                                "profile_id": "core",
                                "bank_id": "personal",
                            },
                        }
                    ]
                },
                "core",
            )

    def test_nested_operation_and_bank_endpoints_match_the_full_target(self):
        expected = EndpointIdentity(
            "core", "http", "127.0.0.1", 7979, "default"
        )
        drifted = {**expected.to_dict(), "port": 7980}
        with self.assertRaisesRegex(PlanError, "operations endpoint identity"):
            _operations(
                {
                    "idle": False,
                    "active": [
                        {
                            "id": "operation-1",
                            "kind": "migration",
                            "status": "running",
                            "endpoint": drifted,
                        }
                    ],
                },
                "core",
                expected,
            )
        with self.assertRaisesRegex(PlanError, "bank endpoint"):
            _observed_banks(
                {"banks": [{"id": "engineering", "endpoint": drifted}]},
                "core",
                expected,
            )


if __name__ == "__main__":
    unittest.main()
