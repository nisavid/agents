import json
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
from types import MappingProxyType
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from hindsight_memory_control_plane.canonical import digest
from hindsight_memory_control_plane.policy import (
    PolicyError,
    observation_scope,
    resolve_policy,
    resolve_session_route,
    validate_durable_policy_input,
    validate_tags,
)
from hindsight_memory_control_plane.providers import (
    PLACEMENTS as PROVIDER_PLACEMENTS,
    ROLES as PROVIDER_ROLES,
    _nonempty,
    CompatibilityReport,
    CompatibilityResult,
    ProviderCompatibilityError,
    validate_provider_compatibility,
)
from hindsight_memory_control_plane.inventory import PLACEMENTS, ROLES


ENGINEERING_RETAIN = (
    "Extract durable engineering knowledge from trusted user/assistant "
    "conversations and structured outcome records: explicit preferences and "
    "corrections, approval boundaries, settled team and workflow conventions, "
    "product and technical decisions with rationale and trade-offs, reusable "
    "procedures, failure chains from symptom through verified fix, and "
    "relationships among people, repositories, systems, issues, pull requests, "
    "releases, clusters, and tools. Preserve provenance and time. Treat "
    "branch, "
    "review, deployment, cluster, service, provider, and quota state as dated "
    "evidence requiring live verification. Ignore greetings, unchosen "
    "brainstorming, session and tool bookkeeping, raw tool output, secrets, "
    "credentials, opaque volatile identifiers, transient local paths, recalled "
    "memory blocks, and unsupported assumptions."
)

PERSONAL_RETAIN = (
    "Apply only to explicitly personal sessions. Extract durable preferences, "
    "goals, commitments, relationships, recurring routines and logistics, "
    "non-work project decisions, and corrections while preserving attribution, "
    "time, confidence, and provenance. Treat schedules, travel, location, and "
    "task status as dated. Exclude credentials, authentication material, "
    "health, medical, financial, legal, and regulated details, raw "
    "external-app "
    "content, unnecessary third-party private facts, pleasantries, agent "
    "mechanics, and recalled memory blocks."
)
CIPHERTEXT_DIGEST = "c" * 64


def private_catalog():
    return {
        "schema_version": 1,
        "contextual_models": [
            {
                "id": "private-review-model",
                "selector_tag": "workflow:synthetic-review",
                "source_filter_tags": ["workflow:synthetic-review"],
            },
            {
                "id": "private-repository-model",
                "selector_tag": "repo:synthetic-repository",
                "source_filter_tags": ["repo:synthetic-repository"],
            },
        ],
        "contextual_model_migrations": [
            {
                "source_id": "private-review-model",
                "disposition": "retain",
                "target_id": "private-review-model",
            },
            {
                "source_id": "private-repository-model",
                "disposition": "retain",
                "target_id": "private-repository-model",
            },
        ],
        "repository_catalog": {
            "canonical": ["repo:synthetic-repository"],
            "aliases": {"project:synthetic": "repo:synthetic-repository"},
            "drop_aliases": ["project:obsolete"],
        },
        "workflow_catalog": {"controlled": ["workflow:synthetic-review"]},
        "privacy": {
            "public_forbidden_literals": [
                "private-review-model",
                "private-repository-model",
                "workflow:synthetic-review",
                "repo:synthetic-repository",
                "project:synthetic",
                "project:obsolete",
            ]
        },
    }


def public_policy():
    return {
        "schema_version": 1,
        "engineering_enabled": True,
        "banks": [
            {
                "id": "engineering",
                "kind": "engineering",
                "authority": "authoritative",
                "writable": True,
            },
            {
                "id": "personal",
                "kind": "personal",
                "authority": "authoritative",
                "writable": True,
            },
            {
                "id": "airlock",
                "kind": "airlock",
                "authority": "none",
                "writable": True,
            },
        ],
        "machine_default": "engineering",
        "workspace_mappings": [
            {
                "selector_id": "workspace:personal",
                "specificity": 10,
                "bank_id": "personal",
            },
            {
                "selector_id": "workspace:engineering",
                "specificity": 5,
                "bank_id": "engineering",
            },
        ],
        "allowed_companions": {
            "engineering": ["personal"],
            "personal": ["engineering"],
            "airlock": [],
        },
    }


class BankPolicyTest(unittest.TestCase):
    def setUp(self):
        self.catalog = private_catalog()
        self.policy = resolve_policy(
            public_policy(),
            self.catalog,
            digest(self.catalog),
            private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
        )

    def test_exact_bank_archetypes_are_immutable_and_closed(self):
        engineering = self.policy.bank("engineering")
        personal = self.policy.bank("personal")
        airlock = self.policy.bank("airlock")
        self.assertEqual(engineering.retain_mission, ENGINEERING_RETAIN)
        self.assertEqual(personal.retain_mission, PERSONAL_RETAIN)
        self.assertEqual(airlock.extraction_mode, "chunk-only")
        self.assertFalse(airlock.observations_enabled)
        self.assertFalse(airlock.entity_extraction_enabled)
        self.assertFalse(airlock.enable_auto_consolidation)
        self.assertFalse(airlock.to_dict()["enable_auto_consolidation"])
        self.assertTrue(engineering.enable_auto_consolidation)
        self.assertTrue(personal.enable_auto_consolidation)
        self.assertEqual(airlock.models, ())
        self.assertIn("untrusted", airlock.retain_mission)
        self.assertIn("no authorization", airlock.reflect_mission)
        self.assertEqual(
            engineering.disposition,
            {"skepticism": 4, "literalism": 3, "empathy": 2},
        )
        self.assertEqual(
            personal.disposition,
            {"skepticism": 4, "literalism": 3, "empathy": 4},
        )
        self.assertEqual(
            engineering.entity_labels["kind"],
            (
                "rule", "principle", "runbook", "decision", "incident",
                "state", "reference",
            ),
        )
        self.assertEqual(
            personal.entity_labels["kind"],
            (
                "preference", "goal", "commitment", "relationship",
                "routine", "logistics", "project", "state", "reference",
            ),
        )
        with self.assertRaises(FrozenInstanceError):
            engineering.retain_mission = "changed"
        with self.assertRaises(TypeError):
            engineering.disposition["empathy"] = 9

    def test_policy_repr_does_not_expose_private_catalog_values(self):
        representation = repr(self.policy)
        for private in self.catalog["privacy"]["public_forbidden_literals"]:
            self.assertNotIn(private, representation)
        for model in self.policy.contextual_models:
            for private in self.catalog["privacy"]["public_forbidden_literals"]:
                self.assertNotIn(private, repr(model))

    def test_empty_controlled_workflow_catalog_is_valid(self):
        catalog = private_catalog()
        catalog["workflow_catalog"]["controlled"] = []
        catalog["contextual_models"] = [catalog["contextual_models"][1]]
        catalog["contextual_model_migrations"] = [
            catalog["contextual_model_migrations"][1]
        ]
        artifact = resolve_policy(
            public_policy(),
            catalog,
            digest(catalog),
            private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
        )
        self.assertEqual(artifact.workflow_tags, frozenset())
        self.assertEqual(
            artifact.repository_tags, frozenset({"repo:synthetic-repository"})
        )

    def test_contextual_model_ids_cannot_shadow_public_bank_models(self):
        catalog = private_catalog()
        catalog["contextual_models"][0]["id"] = "operator-profile"
        catalog["contextual_model_migrations"][0].update(
            {
                "source_id": "operator-profile",
                "target_id": "operator-profile",
            }
        )
        with self.assertRaisesRegex(
            PolicyError, "must not overlap public bank model IDs"
        ):
            resolve_policy(
                public_policy(),
                catalog,
                digest(catalog),
                private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
            )

    def test_contextual_migration_targets_only_emitted_engineering_models(self):
        catalog = private_catalog()
        catalog["contextual_model_migrations"][0] = {
            "source_id": "private-review-model",
            "disposition": "supersede",
            "target_id": "review-pr-playbook",
        }
        with self.assertRaisesRegex(PolicyError, "target is unresolved"):
            resolve_policy(
                public_policy(),
                catalog,
                digest(catalog),
                private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
            )

    def test_models_have_exact_caps_sources_and_controller_only_refresh(self):
        engineering = self.policy.bank("engineering")
        self.assertEqual(
            [(model.id, model.max_tokens) for model in engineering.models],
            [("operator-profile", 1536), ("engineering-principles", 2048)],
        )
        for model in engineering.models:
            self.assertEqual(model.refresh_mode, "delta")
            self.assertEqual(model.source_evidence, ("facts", "observations"))
            self.assertTrue(model.exclude_mental_models)
            self.assertFalse(model.refresh_after_consolidation)
            self.assertIsNone(model.refresh_cron)
        personal = self.policy.bank("personal").models
        self.assertEqual(
            [(model.id, model.max_tokens) for model in personal],
            [("personal-profile", 1024)],
        )
        self.assertEqual(self.policy.contextual_model_cap, 1)
        self.assertTrue(
            all(
                model.strict_source_filter
                for model in self.policy.contextual_models
            )
        )

    def test_defense_tracing_and_cross_bank_writes_are_fail_closed(self):
        for bank_id in ("engineering", "personal", "airlock"):
            bank = self.policy.bank(bank_id)
            self.assertEqual(bank.memory_defense, "sensitive_data")
            self.assertFalse(bank.native_audit_logging)
            self.assertFalse(bank.native_llm_tracing)
        self.assertEqual(self.policy.cross_bank_write_mode, "projection-only")
        projection = self.policy.projection_policy
        self.assertTrue(projection["minimal"])
        self.assertTrue(projection["idempotent"])
        self.assertTrue(projection["provenance_linked"])
        self.assertTrue(projection["independently_deletable"])
        self.assertEqual(
            projection["deny_policy"],
            "source-target-intersection",
        )
        self.assertIn("credential", projection["deny_classes"])
        self.assertIn("recalled_memory_block", projection["deny_classes"])
        self.assertEqual(
            projection["reviewer_bounds"],
            {
                "reviewer_id": "cross-bank-reviewer",
                "provider_binding": "profile-llm",
                "source_data_classes": ("engineering", "personal"),
                "target_data_classes": ("engineering", "personal"),
                "max_input_bytes": 65536,
                "max_output_bytes": 8192,
                "timeout_seconds": 30,
                "no_payload_log": True,
            },
        )
        self.assertEqual(
            projection["stable_identity_fields"],
            (
                "source_session",
                "turn_range",
                "target_bank_ref",
                "policy_version",
            ),
        )
        self.assertTrue(projection["live_notice_required"])
        self.assertTrue(projection["payload_free_ledger"])
        with self.assertRaises(TypeError):
            projection["reviewer_bounds"]["timeout_seconds"] = 60

    def test_public_serialization_discloses_no_private_catalog_literals(self):
        rendered = json.dumps(self.policy.to_dict(), sort_keys=True)
        for private_literal in self.catalog["privacy"][
            "public_forbidden_literals"
        ]:
            self.assertNotIn(private_literal, rendered)

    def test_every_nested_public_field_is_scanned_for_private_literals(self):
        with patch(
            "hindsight_memory_control_plane.policy.PROJECTION_POLICY",
            {"nested": [{"value": "private-review-model"}]},
        ), self.assertRaisesRegex(PolicyError, "private catalog literal"):
            resolve_policy(
                public_policy(),
                self.catalog,
                digest(self.catalog),
                private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
            )
        self.assertEqual(
            self.policy.to_dict()["private_catalog_digest"],
            digest(self.catalog),
        )
        self.assertEqual(
            self.policy.to_dict()["private_catalog_ciphertext_digest"],
            CIPHERTEXT_DIGEST,
        )
        self.assertTrue(
            all(
                ref.startswith("private:")
                for ref in self.policy.to_dict()["contextual_model_refs"]
            )
        )

    def test_privacy_guard_covers_every_migration_source_id(self):
        catalog = private_catalog()
        catalog["contextual_model_migrations"][0] = {
            "source_id": "legacy-private-review-model",
            "disposition": "supersede",
            "target_id": "private-review-model",
        }

        with self.assertRaisesRegex(PolicyError, "privacy guard"):
            resolve_policy(
                public_policy(),
                catalog,
                digest(catalog),
                private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
            )

    def test_policy_digest_binds_every_public_semantic_field(self):
        body = self.policy.to_dict()
        policy_digest = body.pop("policy_digest")
        self.assertEqual(policy_digest, digest(body))

    def test_engineering_disabled_rejects_writes_default_and_routes(self):
        disabled = public_policy()
        disabled["engineering_enabled"] = False
        with self.assertRaisesRegex(PolicyError, "writable engineering"):
            resolve_policy(
                disabled,
                self.catalog,
                digest(self.catalog),
                private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
            )

        disabled["banks"][0]["writable"] = False
        with self.assertRaisesRegex(PolicyError, "engineering machine default"):
            resolve_policy(
                disabled,
                self.catalog,
                digest(self.catalog),
                private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
            )

        disabled["machine_default"] = "personal"
        with self.assertRaisesRegex(PolicyError, "route to engineering"):
            resolve_policy(
                disabled,
                self.catalog,
                digest(self.catalog),
                private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
            )

        disabled["workspace_mappings"] = [
            value
            for value in disabled["workspace_mappings"]
            if value["bank_id"] != "engineering"
        ]
        with self.assertRaisesRegex(PolicyError, "engineering routes"):
            resolve_policy(
                disabled,
                self.catalog,
                digest(self.catalog),
                private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
            )

        disabled["allowed_companions"] = {
            "engineering": [],
            "personal": [],
            "airlock": [],
        }
        resolved = resolve_policy(
            disabled,
            self.catalog,
            digest(self.catalog),
            private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
        )
        self.assertEqual(resolved.machine_default, "personal")

    def test_policy_rejects_a_second_writable_engineering_bank(self):
        public = public_policy()
        public["banks"].append(
            {
                "id": "engineering-replica",
                "kind": "engineering",
                "authority": "replica",
                "writable": True,
            }
        )
        public["allowed_companions"]["engineering-replica"] = []
        with self.assertRaisesRegex(PolicyError, "exactly one authoritative write bank"):
            resolve_policy(
                public,
                self.catalog,
                digest(self.catalog),
                private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
            )

    def test_catalog_and_policy_schemas_are_closed_and_authenticated(
        self,
    ):
        bad_public = public_policy()
        bad_public["caller_bank"] = "personal"
        with self.assertRaisesRegex(PolicyError, "policy keys are closed"):
            resolve_policy(
                bad_public,
                self.catalog,
                digest(self.catalog),
                private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
            )
        bad_catalog = private_catalog()
        bad_catalog["secret_note"] = "must not serialize"
        with self.assertRaisesRegex(PolicyError, "catalog keys are closed"):
            resolve_policy(
                public_policy(),
                bad_catalog,
                digest(bad_catalog),
                private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
            )
        with self.assertRaisesRegex(PolicyError, "catalog digest"):
            resolve_policy(
                public_policy(),
                self.catalog,
                "0" * 64,
                private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
            )
        with self.assertRaisesRegex(PolicyError, "ciphertext"):
            resolve_policy(
                public_policy(),
                self.catalog,
                digest(self.catalog),
                private_catalog_ciphertext_digest="not-a-digest",
            )

    def test_exactly_one_engineering_authority_when_enabled(self):
        for authority in ("none", "replica"):
            config = public_policy()
            config["banks"][0]["authority"] = authority
            with (
                self.subTest(authority=authority),
                self.assertRaisesRegex(PolicyError, "exactly one"),
            ):
                resolve_policy(
                    config,
                    self.catalog,
                    digest(self.catalog),
                    private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
                )
        config = public_policy()
        config["banks"].append(
            {
                "id": "engineering-shadow",
                "kind": "engineering",
                "authority": "authoritative",
                "writable": True,
            }
        )
        config["allowed_companions"]["engineering-shadow"] = []
        with self.assertRaisesRegex(PolicyError, "exactly one"):
            resolve_policy(
                config,
                self.catalog,
                digest(self.catalog),
                private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
            )

    def test_closed_tags_and_exactly_one_semantic_observation_scope(self):
        validate_tags(
            self.policy,
            ("agent:codex", "source:codex-hook", "scope:active", "kind:rule"),
        )
        validate_tags(
            self.policy,
            ("repo:synthetic-repository", "workflow:synthetic-review"),
        )
        with self.assertRaises(PolicyError) as failure:
            validate_tags(self.policy, ("repo:secret-project",))
        self.assertNotIn("secret-project", str(failure.exception))
        for tags in ((None,), ({"repo": "synthetic-repository"},)):
            with self.subTest(tags=tags):
                with self.assertRaisesRegex(PolicyError, "strings"):
                    validate_tags(self.policy, tags)
                with self.assertRaisesRegex(PolicyError, "strings"):
                    observation_scope(self.policy, tags)
        self.assertEqual(
            observation_scope(self.policy, ("repo:synthetic-repository",)),
            "repo:synthetic-repository",
        )
        with self.assertRaisesRegex(PolicyError, "paired"):
            observation_scope(
                self.policy,
                ("repo:synthetic-repository", "scope:active"),
            )
        self.assertEqual(
            observation_scope(self.policy, ("agent:codex", "scope:active")),
            "scope:active",
        )
        self.assertEqual(
            observation_scope(
                self.policy, ("source:file-memory", "scope:archive")
            ),
            "scope:archive",
        )
        self.assertEqual(
            observation_scope(
                self.policy, ("source:airlock-bridge", "scope:airlock")
            ),
            "scope:airlock",
        )
        with self.assertRaisesRegex(PolicyError, "one lifecycle scope"):
            observation_scope(self.policy, ("scope:archive", "scope:airlock"))
        with self.assertRaisesRegex(
            PolicyError, "one semantic observation scope"
        ):
            observation_scope(
                self.policy, ("repo:synthetic-repository", "repo:guessed")
            )

    def test_session_home_must_be_writable_and_authoritative(self):
        for field, value in (("writable", False), ("authority", "replica")):
            config = public_policy()
            config["banks"][1][field] = value
            policy = resolve_policy(
                config,
                self.catalog,
                digest(self.catalog),
                private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
            )
            with self.subTest(field=field), self.assertRaisesRegex(
                PolicyError, "writable authoritative"
            ):
                resolve_session_route(
                    policy,
                    explicit_home_bank="personal",
                    personal_session=True,
                )

    def test_home_and_context_selector_precedence(
        self,
    ):
        route = resolve_session_route(
            self.policy,
            explicit_home_bank="engineering",
            matched_workspaces=("workspace:personal",),
            workflow_selectors=("workflow:synthetic-review",),
            repository_selectors=("repo:synthetic-repository",),
        )
        self.assertEqual(route.home_bank, "engineering")
        self.assertEqual(route.contextual_model_id, "private-review-model")
        self.assertNotIn("private-review-model", repr(route))
        with self.assertRaisesRegex(PolicyError, "explicitly personal"):
            resolve_session_route(
                self.policy, matched_workspaces=("workspace:personal",)
            )
        route = resolve_session_route(
            self.policy,
            matched_workspaces=("workspace:personal",),
            personal_session=True,
        )
        self.assertEqual(route.home_bank, "personal")
        with self.assertRaisesRegex(PolicyError, "explicitly personal"):
            resolve_session_route(self.policy, explicit_home_bank="personal")
        route = resolve_session_route(
            self.policy, explicit_home_bank="personal", personal_session=True
        )
        self.assertEqual(route.home_bank, "personal")
        for selectors in (
            {"workflow_selectors": ("workflow:synthetic-review",)},
            {"repository_selectors": ("repo:synthetic-repository",)},
        ):
            personal = resolve_session_route(
                self.policy,
                explicit_home_bank="personal",
                personal_session=True,
                **selectors,
            )
            self.assertIsNone(personal.contextual_model_id)
            self.assertIsNone(personal.contextual_model_ref)
        with self.assertRaisesRegex(PolicyError, "personal route"):
            resolve_session_route(
                self.policy,
                explicit_home_bank="engineering",
                personal_session=True,
            )
        with self.assertRaisesRegex(PolicyError, "isolated airlock"):
            resolve_session_route(self.policy, explicit_home_bank="airlock")

    def test_airlock_bank_cannot_be_an_ordinary_default_or_workspace_route(
        self,
    ):
        for field in ("machine_default", "workspace_mappings"):
            with self.subTest(field=field):
                config = public_policy()
                if field == "machine_default":
                    config[field] = "airlock"
                else:
                    config[field][0]["bank_id"] = "airlock"
                with self.assertRaisesRegex(PolicyError, "isolated airlock"):
                    resolve_policy(
                        config,
                        self.catalog,
                        digest(self.catalog),
                        private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
                    )

    def test_private_repository_aliases_are_disjoint_from_canonical_values(
        self,
    ):
        for field, value in (
            (
                "aliases",
                {"repo:synthetic-repository": "repo:synthetic-repository"},
            ),
            ("drop_aliases", ["repo:synthetic-repository"]),
        ):
            catalog = private_catalog()
            catalog["repository_catalog"][field] = value
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(PolicyError, "canonical"),
            ):
                resolve_policy(
                    public_policy(),
                    catalog,
                    digest(catalog),
                    private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
                )
        catalog = private_catalog()
        catalog["repository_catalog"]["drop_aliases"] = ["invalid alias"]
        with self.assertRaisesRegex(PolicyError, "alias form"):
            resolve_policy(
                public_policy(),
                catalog,
                digest(catalog),
                private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
            )

    def test_catalog_members_are_typed_before_uniqueness_and_membership(self):
        catalog_cases = {
            "repository tags": lambda value: value["repository_catalog"].update(
                {"canonical": [["repo:invalid"]]}
            ),
            "repository alias target": lambda value: value[
                "repository_catalog"
            ]["aliases"].update({"project:synthetic": ["repo:invalid"]}),
            "repository drop aliases": lambda value: value[
                "repository_catalog"
            ].update({"drop_aliases": [["project:obsolete"]]}),
            "workflow tags": lambda value: value["workflow_catalog"].update(
                {"controlled": [["workflow:invalid"]]}
            ),
            "contextual selector": lambda value: value[
                "contextual_models"
            ][0].update({"selector_tag": ["workflow:invalid"]}),
            "contextual filters": lambda value: value[
                "contextual_models"
            ][0].update({"source_filter_tags": [["workflow:invalid"]]}),
            "migration disposition": lambda value: value[
                "contextual_model_migrations"
            ][0].update({"disposition": ["retain"]}),
            "migration target": lambda value: value[
                "contextual_model_migrations"
            ][0].update({"target_id": ["private-review-model"]}),
            "forbidden literals": lambda value: value["privacy"].update(
                {"public_forbidden_literals": [["private-review-model"]]}
            ),
        }
        for label, mutate in catalog_cases.items():
            catalog = private_catalog()
            mutate(catalog)
            with self.subTest(label=label), self.assertRaises(PolicyError):
                resolve_policy(
                    public_policy(),
                    catalog,
                    digest(catalog),
                    private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
                )

        public_cases = {
            "bank authority": lambda value: value["banks"][0].update(
                {"authority": ["authoritative"]}
            ),
            "bank kind": lambda value: value["banks"][0].update(
                {"kind": ["engineering"]}
            ),
            "workspace bank": lambda value: value["workspace_mappings"][0].update(
                {"bank_id": ["personal"]}
            ),
            "companions": lambda value: value["allowed_companions"].update(
                {"engineering": [["personal"]]}
            ),
        }
        for label, mutate in public_cases.items():
            policy = public_policy()
            mutate(policy)
            catalog = private_catalog()
            with self.subTest(label=label), self.assertRaises(PolicyError):
                resolve_policy(
                    policy,
                    catalog,
                    digest(catalog),
                    private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
                )

    def test_uncertain_context_and_arbitrary_companion_banks_fail_closed(self):
        route = resolve_session_route(
            self.policy,
            workflow_selectors=(
                "workflow:synthetic-review",
                "workflow:unknown",
            ),
            repository_selectors=("repo:synthetic-repository",),
        )
        self.assertIsNone(route.contextual_model_id)
        with self.assertRaisesRegex(PolicyError, "explicitly personal"):
            resolve_session_route(
                self.policy, requested_companions=("personal",)
            )
        with self.assertRaisesRegex(PolicyError, "personal home bank"):
            resolve_session_route(
                self.policy,
                requested_companions=("personal",),
                personal_session=True,
            )
        with self.assertRaisesRegex(PolicyError, "personal companion"):
            resolve_session_route(
                self.policy,
                explicit_home_bank="personal",
                requested_companions=("engineering",),
                personal_session=True,
            )
        with self.assertRaisesRegex(PolicyError, "caller-supplied companion"):
            resolve_session_route(self.policy, requested_companions=("other",))

    def test_companion_banks_must_have_memory_authority(self):
        config = public_policy()
        config["banks"][1]["authority"] = "none"
        config["banks"][1]["writable"] = False
        with self.assertRaisesRegex(PolicyError, "companions.*authority"):
            resolve_policy(
                config,
                self.catalog,
                digest(self.catalog),
                private_catalog_ciphertext_digest=CIPHERTEXT_DIGEST,
            )

    def test_route_members_are_typed_before_membership_and_uniqueness(self):
        cases = (
            {"explicit_home_bank": ["engineering"]},
            {"matched_workspaces": (["workspace:engineering"],)},
            {"requested_companions": (["personal"],)},
            {"workflow_selectors": (["workflow:synthetic-review"],)},
            {"repository_selectors": (["repo:synthetic-repository"],)},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(PolicyError):
                resolve_session_route(self.policy, **kwargs)

    def test_transient_sensitive_and_reversed_inputs_never_become_policy(self):
        forbidden = (
            "transient_state",
            "credential",
            "tool_traffic",
            "injected_memory_block",
            "recently_reversed_convention",
            "health_detail",
            "medical_detail",
            "financial_detail",
            "legal_detail",
            "regulated_detail",
            "raw_external_app_content",
            "third_party_private_fact",
            "opaque_volatile_identifier",
            "transient_local_path",
            "unsupported_assumption",
            "pending_proposal",
            "unchosen_brainstorming",
        )
        for input_class in forbidden:
            with (
                self.subTest(input_class=input_class),
                self.assertRaisesRegex(PolicyError, "durable policy input"),
            ):
                validate_durable_policy_input((input_class,))
        validate_durable_policy_input(("settled_user_rule", "verified_runbook"))
        for invalid in (None, 1, [], {"class": "settled_user_rule"}):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                PolicyError, "must be strings"
            ):
                validate_durable_policy_input((invalid,))

    def test_projection_deny_vocabulary_is_forbidden_as_durable_input(self):
        for input_class in self.policy.projection_policy["deny_classes"]:
            with (
                self.subTest(input_class=input_class),
                self.assertRaisesRegex(PolicyError, "durable policy input"),
            ):
                validate_durable_policy_input((input_class,))

    def test_provider_nonempty_rejects_whitespace_only_values(self):
        for value in ("", " ", "\t\n"):
            with self.subTest(value=value), self.assertRaisesRegex(
                ProviderCompatibilityError, "non-empty string"
            ):
                _nonempty(value, "provider field")


def provider(
    provider_id,
    role,
    *,
    artifact_id,
    placement="local",
    state="current",
    gates=None,
    fallback=None,
    reasoning_effort="default",
    api=None,
    revision="rev-1",
    active_revision="rev-1",
    active_artifact_id=None,
):
    if api is None:
        api = (
            "openai-compatible"
            if role in {"llm", "embedding"}
            else "cohere-compatible"
        )
    return {
        "id": provider_id,
        "role": role,
        "placement": placement,
        "data_classes": ["engineering", "personal"],
        "transport": {
            "protocol": "https" if placement != "local" else "loopback",
            "api": api,
        },
        "tls": {"server_name": "provider.invalid", "trust_roots": ["system"]}
        if placement == "private-remote"
        else None,
        "credential": None
        if placement == "local"
        else {"mode": "keychain", "locator": f"keychain:{provider_id}"},
        "readiness": {
            "ready": True,
            "version_compatible": True,
            "license_ready": True,
        },
        "model": {
            "artifact_id": artifact_id,
            "active_artifact_id": (
                artifact_id
                if active_artifact_id is None
                else active_artifact_id
            ),
            "revision": revision,
            "active_revision": active_revision,
            "reasoning_effort": (
                reasoning_effort
                if role == "llm"
                else None
            ),
        },
        "contract": {
            "readiness_probe": {
                "kind": "http" if placement != "local" else "process",
                "target": "health",
            },
            "timeout_seconds": 30,
            "no_payload_log": True,
            "api_compatible": True,
        },
        "state": state,
        "gates": {} if gates is None else gates,
        "fallback": fallback,
    }


class ProviderCompatibilityTest(unittest.TestCase):
    def test_provider_vocabulary_is_shared_with_inventory(self):
        self.assertIs(PROVIDER_ROLES, ROLES)
        self.assertIs(PROVIDER_PLACEMENTS, PLACEMENTS)

    def setUp(self):
        self.providers = [
            provider(
                "current-llm",
                "llm",
                artifact_id="current-llm-model",
                placement="third-party-hosted",
            ),
            provider(
                "desired-llm",
                "llm",
                artifact_id="candidate-llm-model",
                placement="third-party-hosted",
                state="desired",
                gates={"adapter_contract": False},
            ),
            provider(
                "openai-embedding",
                "embedding",
                artifact_id="text-embedding-3-small",
                placement="third-party-hosted",
            ),
            provider(
                "current-reranker",
                "reranking",
                artifact_id="current-reranker-model",
                state="fallback",
            ),
            provider(
                "desired-reranker",
                "reranking",
                artifact_id="candidate-reranker-model",
                state="desired",
                gates={
                    "protocol_adapter_compatibility": False,
                    "private_benchmark": False,
                },
                fallback="current-reranker",
            ),
        ]
        self.profile = {
            "id": "core",
            "data_classes": ["engineering", "personal"],
            "roles": {
                "llm": {
                    "current": "current-llm",
                    "desired": "desired-llm",
                },
                "embedding": {"current": "openai-embedding"},
                "reranking": {
                    "current": "current-reranker",
                    "desired": "desired-reranker",
                },
            },
            "allowed_placements": {
                "engineering": [
                    "local",
                    "third-party-hosted",
                    "private-remote",
                ],
                "personal": ["local", "third-party-hosted", "private-remote"],
            },
            "llm_failover": ["current-llm"],
        }
        self.storage = {
            "populated": True,
            "embedding_artifact_id": "text-embedding-3-small",
            "embedding_revision": "rev-1",
        }

    def validate(self, providers=None, profile=None, storage=None, switches=()):
        return validate_provider_compatibility(
            self.profile if profile is None else profile,
            self.providers if providers is None else providers,
            self.storage if storage is None else storage,
            revision_switches=switches,
        )

    def test_storage_state_accepts_read_only_mapping(self):
        report = self.validate(storage=MappingProxyType(self.storage))
        self.assertEqual(report.profile_id, "core")

    def test_provider_enums_and_llm_failover_fail_closed_on_non_strings(self):
        mutations = (
            lambda value: value.update({"role": []}),
            lambda value: value.update({"placement": {}}),
            lambda value: value["transport"].update({"protocol": []}),
            lambda value: value["transport"].update({"api": {}}),
            lambda value: value["contract"]["readiness_probe"].update(
                {"kind": []}
            ),
            lambda value: value.update({"state": []}),
        )
        for mutate in mutations:
            providers = [dict(value) for value in self.providers]
            providers[0] = {
                **self.providers[0],
                "transport": dict(self.providers[0]["transport"]),
                "contract": {
                    **self.providers[0]["contract"],
                    "readiness_probe": dict(
                        self.providers[0]["contract"]["readiness_probe"]
                    ),
                },
            }
            mutate(providers[0])
            with self.subTest(provider=providers[0]), self.assertRaises(
                ProviderCompatibilityError
            ):
                self.validate(providers=providers)

        providers = [dict(value) for value in self.providers]
        providers[0] = {
            **self.providers[0],
            "credential": {"mode": [], "locator": "keychain:current-llm"},
        }
        with self.assertRaises(ProviderCompatibilityError):
            self.validate(providers=providers)

        with self.assertRaises(ProviderCompatibilityError):
            self.validate(profile={**self.profile, "llm_failover": [[]]})
        invalid_placements = {
            **self.profile,
            "allowed_placements": {
                **self.profile["allowed_placements"],
                "engineering": [[]],
            },
        }
        with self.assertRaises(ProviderCompatibilityError):
            self.validate(profile=invalid_placements)

    def test_provider_placement_requires_exact_transport_security(self):
        for placement, protocol in (
            ("local", "https"),
            ("third-party-hosted", "loopback"),
            ("private-remote", "loopback"),
        ):
            providers = [dict(value) for value in self.providers]
            providers[0] = provider(
                "current-llm",
                "llm",
                artifact_id="current-llm-model",
                placement=placement,
            )
            providers[0]["transport"]["protocol"] = protocol
            providers[0]["credential"] = (
                None
                if placement == "local"
                else {
                    "mode": "keychain",
                    "locator": "keychain:current-llm",
                }
            )
            with self.subTest(placement=placement), self.assertRaisesRegex(
                ProviderCompatibilityError, "loopback|https"
            ):
                self.validate(providers=providers)

    def test_reranker_fallback_graph_rejects_cycles(self):
        providers = [dict(value) for value in self.providers]
        current_index = next(
            index
            for index, value in enumerate(providers)
            if value["id"] == "current-reranker"
        )
        providers[current_index] = {
            **providers[current_index],
            "fallback": "secondary-reranker",
        }
        providers.append(
            provider(
                "secondary-reranker",
                "reranking",
                artifact_id="secondary-reranker-model",
                state="fallback",
                fallback="current-reranker",
            )
        )
        with self.assertRaisesRegex(
            ProviderCompatibilityError, "fallback graph contains a cycle"
        ):
            self.validate(providers=providers)

    def test_roles_independent_and_desired_candidates_blocked(
        self,
    ):
        report = self.validate()
        self.assertEqual(
            report.role_bindings["llm"],
            {"current": "current-llm", "desired": "desired-llm"},
        )
        self.assertEqual(report.result("current-llm").state, "current")
        self.assertTrue(report.result("current-llm").activatable)
        self.assertEqual(report.result("current-reranker").state, "fallback")
        spark = report.result("desired-llm")
        self.assertEqual(
            (spark.state, spark.activatable), ("blocked_candidate", False)
        )
        self.assertIn(
            "adapter_contract", spark.blocked_by
        )
        mem = report.result("desired-reranker")
        self.assertEqual(
            (mem.state, mem.activatable), ("blocked_candidate", False)
        )
        self.assertEqual(
            mem.blocked_by,
            ("private_benchmark", "protocol_adapter_compatibility"),
        )
        self.assertEqual(
            report.reranking_disposition,
            {
                "state": "fallback",
                "provider_id": "current-reranker",
                "visible_degradation": True,
            },
        )
        with self.assertRaises(FrozenInstanceError):
            mem.activatable = True

    def test_current_binding_preserves_a_healthy_fallback_provider_state(self):
        report = self.validate()

        self.assertEqual(
            report.role_bindings["reranking"]["current"],
            "current-reranker",
        )
        self.assertEqual(report.result("current-reranker").state, "fallback")
        self.assertEqual(
            report.reranking_disposition,
            {
                "state": "fallback",
                "provider_id": "current-reranker",
                "visible_degradation": True,
            },
        )

    def test_role_bindings_and_custom_gates_have_canonical_order(self):
        profile = {
            **self.profile,
            "roles": {
                **self.profile["roles"],
                "llm": {
                    "desired": "desired-llm",
                    "current": "current-llm",
                },
            },
        }
        providers = list(self.providers)
        providers[1] = {
            **providers[1],
            "gates": {"z-last": False, "a-first": False},
        }
        report = self.validate(providers=providers, profile=profile)
        self.assertEqual(
            tuple(report.role_bindings["llm"]), ("current", "desired")
        )
        self.assertEqual(
            report.result("desired-llm").blocked_by,
            ("a-first", "z-last"),
        )

    def test_desired_candidates_require_declared_gate_evidence(self):
        for index in (1, 4):
            providers = list(self.providers)
            providers[index] = {**providers[index], "gates": {}}
            with (
                self.subTest(provider=providers[index]["id"]),
                self.assertRaisesRegex(
                    ProviderCompatibilityError,
                    "declare candidate gates",
                ),
            ):
                self.validate(providers=providers)

    def test_desired_candidates_are_optional(self):
        for role in ("llm", "reranking"):
            profile = {
                **self.profile,
                "roles": {
                    **self.profile["roles"],
                    role: {"current": self.profile["roles"][role]["current"]},
                },
            }
            with self.subTest(role=role):
                report = self.validate(profile=profile)
                self.assertNotIn("desired", report.role_bindings[role])

    def test_role_bindings_require_nonempty_string_provider_ids(self):
        for value in (None, "", []):
            with self.subTest(value=value):
                profile = {
                    **self.profile,
                    "roles": {
                        **self.profile["roles"],
                        "llm": {"current": value},
                    },
                }
                with self.assertRaisesRegex(
                    ProviderCompatibilityError, "bounded identifier"
                ):
                    self.validate(profile=profile)

    def test_all_provider_identity_inputs_require_bounded_identifiers(self):
        invalid = "x" * 129
        mutations = (
            lambda providers, profile, storage, switches: providers[0].update(
                {"id": invalid}
            ),
            lambda providers, profile, storage, switches: providers[0][
                "model"
            ].update({"artifact_id": invalid}),
            lambda providers, profile, storage, switches: providers[0][
                "model"
            ].update({"active_artifact_id": invalid}),
            lambda providers, profile, storage, switches: providers[0][
                "model"
            ].update({"revision": invalid}),
            lambda providers, profile, storage, switches: providers[0][
                "model"
            ].update({"active_revision": invalid}),
            lambda providers, profile, storage, switches: providers[4].update(
                {"fallback": invalid}
            ),
            lambda providers, profile, storage, switches: providers[1].update(
                {"gates": {invalid: False}}
            ),
            lambda providers, profile, storage, switches: profile.update(
                {"id": invalid}
            ),
            lambda providers, profile, storage, switches: profile["roles"][
                "llm"
            ].update({"current": invalid}),
            lambda providers, profile, storage, switches: profile.update(
                {"llm_failover": [invalid]}
            ),
            lambda providers, profile, storage, switches: storage.update(
                {"embedding_artifact_id": invalid}
            ),
            lambda providers, profile, storage, switches: storage.update(
                {"embedding_revision": invalid}
            ),
        )
        for mutate in mutations:
            providers = deepcopy(self.providers)
            profile = deepcopy(self.profile)
            storage = deepcopy(self.storage)
            switches = []
            mutate(providers, profile, storage, switches)
            with self.subTest(mutate=mutate), self.assertRaisesRegex(
                ProviderCompatibilityError, "bounded identifier"
            ):
                self.validate(
                    providers=providers,
                    profile=profile,
                    storage=storage,
                    switches=switches,
                )

    def test_revision_switch_identity_fields_require_bounded_identifiers(self):
        base = {
            "provider_id": "current-reranker",
            "from_artifact_id": "current-reranker-model",
            "from_revision": "rev-1",
            "to_artifact_id": "current-reranker-model",
            "to_revision": "rev-2",
            "blue_green_rebuild": False,
            "approved": True,
        }
        for field in (
            "provider_id",
            "from_artifact_id",
            "from_revision",
            "to_artifact_id",
            "to_revision",
        ):
            with self.subTest(field=field), self.assertRaisesRegex(
                ProviderCompatibilityError, "bounded identifier"
            ):
                self.validate(switches=({**base, field: "x" * 129},))

    def test_compatibility_outputs_require_bounded_identifiers(self):
        fields = {
            "provider_id": "provider",
            "role": "llm",
            "state": "current",
            "compatible": True,
            "activatable": True,
            "blocked_by": (),
            "fallback_provider_id": None,
            "placement": "local",
            "artifact_id": "artifact",
            "revision": "revision",
        }
        for field in (
            "provider_id",
            "fallback_provider_id",
            "artifact_id",
            "revision",
        ):
            with self.subTest(field=field), self.assertRaisesRegex(
                ProviderCompatibilityError, "bounded identifier"
            ):
                CompatibilityResult(**{**fields, field: "x" * 129})
        with self.assertRaisesRegex(
            ProviderCompatibilityError, "bounded identifier"
        ):
            CompatibilityResult(**{**fields, "blocked_by": ("x" * 129,)})

        valid_result = CompatibilityResult(**fields)
        with self.assertRaisesRegex(
            ProviderCompatibilityError, "bounded identifier"
        ):
            CompatibilityReport(
                profile_id="profile",
                role_bindings={"llm": {"current": "x" * 129}},
                results=(valid_result,),
                reranking_disposition={
                    "state": "disabled",
                    "provider_id": None,
                    "visible_degradation": True,
                },
            )

    def test_llm_failover_is_optional(self):
        profile = {**self.profile, "llm_failover": []}
        report = self.validate(profile=profile)
        self.assertEqual(
            report.role_bindings["llm"]["current"], "current-llm"
        )

    def test_llm_failover_providers_are_evaluated_in_declared_order(self):
        providers = [
            *self.providers,
            provider(
                "failover-a", "llm", artifact_id="fallback-a",
                state="fallback",
            ),
            provider(
                "failover-b", "llm", artifact_id="fallback-b",
                state="fallback",
            ),
        ]
        profile = {
            **self.profile,
            "llm_failover": ["failover-b", "failover-a", "current-llm"],
        }
        report = self.validate(providers=providers, profile=profile)
        result_ids = [result.provider_id for result in report.results]
        self.assertLess(
            result_ids.index("failover-b"), result_ids.index("failover-a")
        )
        self.assertIn("current-llm", result_ids)

    def test_placement_data_class_and_role_mismatch_fail_closed(self):
        providers = list(self.providers)
        providers[0] = {**providers[0], "data_classes": ["engineering"]}
        with self.assertRaisesRegex(ProviderCompatibilityError, "personal"):
            self.validate(providers=providers)
        providers = list(self.providers)
        model = {**providers[0]["model"], "reasoning_effort": None}
        providers[0] = {
            **providers[0],
            "role": "embedding",
            "model": model,
        }
        with self.assertRaisesRegex(ProviderCompatibilityError, "role"):
            self.validate(providers=providers)

    def test_non_string_data_classes_fail_closed_without_type_errors(self):
        for malformed in ({"engineering": True}, ["engineering"]):
            with self.subTest(malformed=malformed):
                providers = list(self.providers)
                providers[0] = {
                    **providers[0],
                    "data_classes": [malformed, "personal"],
                }
                with self.assertRaises(ProviderCompatibilityError):
                    self.validate(providers=providers)

                profile = {**self.profile, "data_classes": [malformed]}
                with self.assertRaises(ProviderCompatibilityError):
                    self.validate(profile=profile)

    def test_unknown_data_classes_fail_closed_for_providers_and_profiles(self):
        providers = list(self.providers)
        providers[0] = {**providers[0], "data_classes": ["medical"]}
        with self.assertRaises(ProviderCompatibilityError):
            self.validate(providers=providers)

        profile = {**self.profile, "data_classes": ["medical"]}
        with self.assertRaises(ProviderCompatibilityError):
            self.validate(profile=profile)

    def test_private_remote_requires_tls_identity_and_trust_roots(self):
        cases = (
            (None, "TLS identity and trust roots"),
            ({"trust_roots": ["system"]}, "TLS identity"),
            ({"server_name": "provider.invalid"}, "TLS trust roots"),
            ({"server_name": "", "trust_roots": ["system"]}, "server identity"),
            ({"server_name": "provider.invalid", "trust_roots": []}, "trust roots"),
            ({"server_name": [], "trust_roots": ["system"]}, "server identity"),
            ({"server_name": "provider.invalid", "trust_roots": [""]}, "trust roots"),
        )
        for tls, message in cases:
            providers = list(self.providers)
            providers[0] = {
                **providers[0],
                "placement": "private-remote",
                "tls": tls,
            }
            with self.subTest(tls=tls), self.assertRaisesRegex(
                ProviderCompatibilityError, message
            ):
                self.validate(providers=providers)

    def test_non_private_tls_mapping_requires_identity_and_trust_roots(self):
        cases = (
            ({}, "TLS identity"),
            ({"server_name": "provider.invalid"}, "TLS trust roots"),
            (
                {"server_name": "", "trust_roots": ["system"]},
                "server identity",
            ),
            (
                {"server_name": " ", "trust_roots": ["system"]},
                "server identity",
            ),
            (
                {"server_name": "provider.invalid", "trust_roots": []},
                "trust roots",
            ),
            (
                {"server_name": "provider.invalid", "trust_roots": [""]},
                "trust roots",
            ),
            (
                {"server_name": "provider.invalid", "trust_roots": [" "]},
                "trust roots",
            ),
        )
        for placement in ("local", "third-party-hosted"):
            for tls, message in cases:
                providers = list(self.providers)
                provider_value = dict(providers[0])
                provider_value["placement"] = placement
                provider_value["transport"] = {
                    **provider_value["transport"],
                    "protocol": (
                        "loopback" if placement == "local" else "https"
                    ),
                }
                provider_value["tls"] = tls
                if placement == "local":
                    provider_value["credential"] = None
                else:
                    provider_value["credential"] = {
                        "mode": "keychain",
                        "locator": "keychain:test-provider",
                    }
                providers[0] = provider_value
                with self.subTest(
                    placement=placement, tls=tls
                ), self.assertRaisesRegex(
                    ProviderCompatibilityError, message
                ):
                    self.validate(providers=providers)

    def test_credentials_are_locators_only_and_never_values(self):
        providers = list(self.providers)
        providers[0] = {
            **providers[0],
            "credential": {
                "mode": "keychain",
                "locator": "keychain:item",
                "value": "secret",
            },
        }
        with self.assertRaisesRegex(ProviderCompatibilityError, "credential"):
            self.validate(providers=providers)
        providers = list(self.providers)
        providers[3] = {
            **providers[3],
            "credential": {"mode": "keychain", "locator": ""},
        }
        with self.assertRaisesRegex(
            ProviderCompatibilityError, "credential locator"
        ):
            self.validate(providers=providers)
        providers = list(self.providers)
        providers[0] = {
            **providers[0],
            "credential": {
                "mode": "keychain",
                "locator": "inline-secret",
            },
        }
        with self.assertRaisesRegex(
            ProviderCompatibilityError,
            "locator shape",
        ):
            self.validate(providers=providers)

    def test_provider_contract_is_complete_and_fail_closed(self):
        for key, value in (
            ("readiness_probe", None),
            ("timeout_seconds", 0),
            ("no_payload_log", False),
            ("api_compatible", False),
        ):
            providers = list(self.providers)
            contract = dict(providers[0]["contract"])
            contract[key] = value
            providers[0] = {**providers[0], "contract": contract}
            with (
                self.subTest(key=key),
                self.assertRaises(ProviderCompatibilityError),
            ):
                self.validate(providers=providers)

    def test_readiness_version_and_license_are_independent_gates(self):
        for gate in ("ready", "version_compatible", "license_ready"):
            providers = list(self.providers)
            readiness = dict(providers[0]["readiness"])
            readiness[gate] = False
            providers[0] = {**providers[0], "readiness": readiness}
            with self.subTest(gate=gate):
                result = self.validate(providers=providers).result(
                    "current-llm"
                )
                self.assertFalse(result.activatable)
                self.assertIn(gate, result.blocked_by)

    def test_populated_embedding_identity_requires_explicit_blue_green_switch(
        self,
    ):
        providers = list(self.providers)
        providers[2] = provider(
            "openai-embedding",
            "embedding",
            artifact_id="other-embedding",
            placement="third-party-hosted",
            revision="rev-2",
            active_revision="rev-1",
        )
        blocked = self.validate(providers=providers).result("openai-embedding")
        self.assertFalse(blocked.activatable)
        self.assertIn("embedding_identity_immutable", blocked.blocked_by)
        switches = (
            {
                "provider_id": "openai-embedding",
                "from_artifact_id": "text-embedding-3-small",
                "from_revision": "rev-1",
                "to_artifact_id": "other-embedding",
                "to_revision": "rev-2",
                "blue_green_rebuild": True,
                "approved": True,
            },
        )
        allowed = self.validate(providers=providers, switches=switches).result(
            "openai-embedding"
        )
        self.assertTrue(allowed.activatable)

    def test_embedding_switch_must_match_every_identity_field_and_real_drift(self):
        providers = list(self.providers)
        providers[2] = provider(
            "openai-embedding",
            "embedding",
            artifact_id="other-embedding",
            placement="third-party-hosted",
            revision="rev-2",
            active_revision="rev-1",
        )
        exact = {
            "provider_id": "openai-embedding",
            "from_artifact_id": "text-embedding-3-small",
            "from_revision": "rev-1",
            "to_artifact_id": "other-embedding",
            "to_revision": "rev-2",
            "blue_green_rebuild": True,
            "approved": True,
        }
        mismatches = (
            ("from_artifact_id", "wrong-active-artifact"),
            ("from_revision", "wrong-active-revision"),
            ("to_artifact_id", "wrong-desired-artifact"),
            ("to_revision", "wrong-desired-revision"),
        )
        for field, value in mismatches:
            switch = {**exact, field: value}
            with self.subTest(field=field):
                result = self.validate(
                    providers=providers, switches=(switch,)
                ).result("openai-embedding")
                self.assertFalse(result.activatable)
                self.assertIn("embedding_identity_immutable", result.blocked_by)

        no_drift = list(self.providers)
        with self.assertRaisesRegex(
            ProviderCompatibilityError, "revision switch"
        ):
            self.validate(providers=no_drift, switches=(exact,))

    def test_populated_storage_drift_is_considered_before_switch_rejection(self):
        providers = list(self.providers)
        providers[2] = provider(
            "openai-embedding",
            "embedding",
            artifact_id="other-embedding",
            active_artifact_id="other-embedding",
            placement="third-party-hosted",
            revision="rev-2",
            active_revision="rev-2",
        )
        switch = {
            "provider_id": "openai-embedding",
            "from_artifact_id": "text-embedding-3-small",
            "from_revision": "rev-1",
            "to_artifact_id": "other-embedding",
            "to_revision": "rev-2",
            "blue_green_rebuild": True,
            "approved": True,
        }
        result = self.validate(
            providers=providers, switches=(switch,)
        ).result("openai-embedding")
        self.assertTrue(result.activatable)

    def test_populated_storage_is_the_effective_embedding_active_identity(self):
        providers = list(self.providers)
        providers[2] = provider(
            "openai-embedding",
            "embedding",
            artifact_id="text-embedding-3-small",
            active_artifact_id="stale-provider-active",
            placement="third-party-hosted",
            revision="rev-1",
            active_revision="stale-provider-revision",
        )
        result = self.validate(providers=providers).result(
            "openai-embedding"
        )
        self.assertTrue(result.activatable)
        self.assertNotIn("revision_switch_not_approved", result.blocked_by)

        no_op_switch = {
            "provider_id": "openai-embedding",
            "from_artifact_id": "stale-provider-active",
            "from_revision": "stale-provider-revision",
            "to_artifact_id": "text-embedding-3-small",
            "to_revision": "rev-1",
            "blue_green_rebuild": True,
            "approved": True,
        }
        with self.assertRaisesRegex(
            ProviderCompatibilityError, "no target drift"
        ):
            self.validate(providers=providers, switches=(no_op_switch,))

    def test_revision_drift_requires_an_exact_explicit_switch(self):
        providers = list(self.providers)
        providers[3] = provider(
            "current-reranker",
            "reranking",
            artifact_id="current-reranker-model",
            state="fallback",
            revision="rev-2",
            active_revision="rev-1",
        )
        result = self.validate(providers=providers).result("current-reranker")
        self.assertIn("revision_switch_not_approved", result.blocked_by)

    def test_artifact_drift_requires_an_exact_explicit_switch(self):
        providers = list(self.providers)
        providers[3] = provider(
            "current-reranker",
            "reranking",
            artifact_id="current-reranker-model",
            state="fallback",
            active_artifact_id="previous-reranker",
        )
        result = self.validate(providers=providers).result("current-reranker")
        self.assertIn("revision_switch_not_approved", result.blocked_by)

    def test_dangling_or_irrelevant_switches_fail_closed(self):
        switch = {
            "provider_id": "missing-provider",
            "from_artifact_id": "old",
            "from_revision": "old-rev",
            "to_artifact_id": "new",
            "to_revision": "new-rev",
            "blue_green_rebuild": False,
            "approved": True,
        }
        with self.assertRaisesRegex(
            ProviderCompatibilityError,
            "revision switch",
        ):
            self.validate(switches=(switch,))

    def test_consumer_selects_current_provider_artifacts(self):
        providers = list(self.providers)
        providers[0] = provider(
            "current-llm",
            "llm",
            artifact_id="unexpected-current-llm",
            placement="third-party-hosted",
        )
        report = self.validate(providers=providers)
        self.assertEqual(
            report.result("current-llm").artifact_id,
            "unexpected-current-llm",
        )

        providers = list(self.providers)
        providers[3] = provider(
            "current-reranker",
            "reranking",
            artifact_id="unexpected-current-reranker",
            state="fallback",
        )
        report = self.validate(providers=providers)
        self.assertEqual(
            report.result("current-reranker").artifact_id,
            "unexpected-current-reranker",
        )

    def test_incompatible_reranker_without_fallback_is_visibly_disabled(self):
        providers = list(self.providers)
        providers[3] = {
            **providers[3],
            "readiness": {
                "ready": False,
                "version_compatible": True,
                "license_ready": True,
            },
        }
        providers[4] = {**providers[4], "fallback": None}
        report = self.validate(providers=providers)
        self.assertEqual(
            report.reranking_disposition,
            {
                "state": "disabled",
                "provider_id": None,
                "visible_degradation": True,
            },
        )
    def test_healthy_current_reranker_wins_over_unbound_desired_fallback(self):
        providers = [
            *self.providers,
            provider(
                "alternate-reranker",
                "reranking",
                artifact_id="alternate-reranker-artifact",
                state="fallback",
            ),
        ]
        providers[4] = {
            **providers[4],
            "fallback": "alternate-reranker",
        }

        report = self.validate(providers=providers)

        self.assertTrue(report.result("alternate-reranker").activatable)
        self.assertEqual(
            report.reranking_disposition,
            {
                "state": "fallback",
                "provider_id": "current-reranker",
                "visible_degradation": True,
            },
        )

    def test_healthy_desired_fallback_is_used_even_when_candidate_is_activatable(self):
        providers = [
            *self.providers,
            provider(
                "alternate-reranker",
                "reranking",
                artifact_id="alternate-reranker-artifact",
                state="fallback",
            ),
        ]
        providers[3] = {
            **providers[3],
            "readiness": {
                "ready": False,
                "version_compatible": True,
                "license_ready": True,
            },
            "fallback": None,
        }
        providers[4] = {
            **providers[4],
            "gates": {
                "protocol_adapter_compatibility": True,
                "private_benchmark": True,
            },
            "fallback": "alternate-reranker",
        }

        report = self.validate(providers=providers)

        self.assertTrue(report.result("desired-reranker").activatable)
        self.assertEqual(
            report.reranking_disposition,
            {
                "state": "fallback",
                "provider_id": "alternate-reranker",
                "visible_degradation": True,
            },
        )

    def test_incompatible_current_reranker_uses_its_activatable_fallback(self):
        providers = [
            *self.providers,
            provider(
                "alternate-reranker",
                "reranking",
                artifact_id="alternate-reranker-artifact",
                state="fallback",
            ),
        ]
        providers[3] = {
            **providers[3],
            "readiness": {
                "ready": False,
                "version_compatible": True,
                "license_ready": True,
            },
            "fallback": "alternate-reranker",
        }

        report = self.validate(providers=providers)

        self.assertEqual(
            report.reranking_disposition,
            {
                "state": "fallback",
                "provider_id": "alternate-reranker",
                "visible_degradation": True,
            },
        )

    def test_current_reranker_fallback_precedes_desired_candidate_fallback(self):
        providers = [
            *self.providers,
            provider(
                "current-safety-reranker",
                "reranking",
                artifact_id="current-safety-artifact",
                state="fallback",
            ),
            provider(
                "desired-safety-reranker",
                "reranking",
                artifact_id="desired-safety-artifact",
                state="fallback",
            ),
        ]
        providers[3] = {
            **providers[3],
            "readiness": {
                "ready": False,
                "version_compatible": True,
                "license_ready": True,
            },
            "fallback": "current-safety-reranker",
        }
        providers[4] = {
            **providers[4],
            "fallback": "desired-safety-reranker",
        }

        report = self.validate(providers=providers)

        self.assertEqual(
            report.reranking_disposition,
            {
                "state": "fallback",
                "provider_id": "current-safety-reranker",
                "visible_degradation": True,
            },
        )

    def test_reranker_fallback_must_exist_and_serve_reranking(self):
        for fallback in ("missing-provider", "current-llm", "desired-reranker"):
            providers = list(self.providers)
            providers[4] = {**providers[4], "fallback": fallback}
            with (
                self.subTest(fallback=fallback),
                self.assertRaisesRegex(
                    ProviderCompatibilityError, "reranker fallback"
                ),
            ):
                self.validate(providers=providers)

    def test_reranker_fallback_traverses_complete_reachable_chain(self):
        providers = [
            *self.providers,
            provider(
                "fallback-a",
                "reranking",
                artifact_id="fallback-a-artifact",
                state="fallback",
                fallback="fallback-b",
            ),
            provider(
                "fallback-b",
                "reranking",
                artifact_id="fallback-b-artifact",
                state="fallback",
            ),
        ]
        providers[3] = {
            **providers[3],
            "readiness": {
                "ready": False,
                "version_compatible": True,
                "license_ready": True,
            },
            "fallback": "fallback-a",
        }
        providers[5] = {
            **providers[5],
            "readiness": {
                "ready": False,
                "version_compatible": True,
                "license_ready": True,
            },
        }
        report = self.validate(providers=providers)
        self.assertEqual(
            report.reranking_disposition,
            {
                "state": "fallback",
                "provider_id": "fallback-b",
                "visible_degradation": True,
            },
        )
        current_only_profile = {
            **self.profile,
            "roles": {
                **self.profile["roles"],
                "reranking": {"current": "current-reranker"},
            },
        }
        current_only = self.validate(
            providers=providers, profile=current_only_profile
        )
        self.assertEqual(
            current_only.reranking_disposition,
            {
                "state": "fallback",
                "provider_id": "fallback-b",
                "visible_degradation": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
