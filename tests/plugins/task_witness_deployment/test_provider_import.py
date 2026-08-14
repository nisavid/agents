from __future__ import annotations

import ast
import copy
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType
from unittest import mock

from ._support import (
    PLUGIN,
    PROVIDER_CONTRACT,
    SMOKE_VALIDATOR_SOURCE,
    TRUST_CONTRACT,
    ProviderFixture,
    assert_private_regular,
    canonical_bytes,
    canonical_document,
    load_deployment_module,
    sha256,
    validator_identity,
)


class ProviderImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.provider = ProviderFixture(self.root / "provider")
        self.trust = self.root / "trust"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def deployment(self):
        return load_deployment_module()

    def materialize(self, provider: ProviderFixture | None = None, trust: Path | None = None):
        provider = self.provider if provider is None else provider
        trust = self.trust if trust is None else trust
        return self.deployment().materialize_provider(provider.root, trust)

    def compose(self, providers=(), trust: Path | None = None):
        trust = self.trust if trust is None else trust
        deployment = self.deployment()
        deployment.materialize_intrinsic_smoke_provider(trust)
        deployment.materialize_trust_context(providers, trust)
        return deployment.compose_trust_context(providers, trust)

    def assert_rejected(self, pattern: str) -> None:
        deployment = self.deployment()
        with self.assertRaisesRegex(deployment.DeploymentError, pattern):
            deployment.materialize_provider(self.provider.root, self.trust)

    def resize_validator_modules(
        self,
        provider: ProviderFixture,
        total_bytes: int,
    ) -> None:
        targets = (total_bytes // 2, total_bytes - (total_bytes // 2))
        for path, target, declaration in zip(
            (provider.entrypoint, provider.helper),
            targets,
            provider.validator_modules,
        ):
            original = path.read_bytes()
            self.assertGreaterEqual(target, len(original) + 2)
            raw = original + b"#" + (b"x" * (target - len(original) - 2)) + b"\n"
            path.write_bytes(raw)
            declaration["length"] = len(raw)
            declaration["sha256"] = sha256(raw)
        provider.refresh_validator_identity()
        provider.write()

    def integer_source_constant(self, relative: str, name: str) -> int:
        tree = ast.parse((PLUGIN / relative).read_text(encoding="utf-8"))

        def evaluate(node: ast.expr) -> int:
            if isinstance(node, ast.Constant) and type(node.value) is int:
                return node.value
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
                return evaluate(node.left) * evaluate(node.right)
            raise AssertionError(f"{relative} {name} is not a fixed integer")

        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == name
                    for target in node.targets
                )
            ):
                return evaluate(node.value)
        raise AssertionError(f"{relative} does not define {name}")

    def test_materializes_one_multimodule_provider(self) -> None:
        result = self.materialize()

        self.assertIsInstance(result, self.deployment().ProviderMaterialization)
        self.assertEqual(result.plugin_id, "demo-plugin")
        self.assertEqual(len(result.producers), 1)
        self.assertEqual(len(result.issuers), 1)
        self.assertEqual(len(result.validators), 1)
        self.assertEqual([item.name for item in result.modules], ["validator", "helper"])
        self.assertEqual(result.validators[0]["entrypoint"], "validator")

    def test_absent_provider_declaration_registers_nothing(self) -> None:
        self.provider.provider_path.unlink()

        self.assertIsNone(self.materialize())

    def test_rejects_noncanonical_and_duplicate_key_json(self) -> None:
        canonical = canonical_document(self.provider.value)
        variants = (
            json.dumps(self.provider.value, indent=2).encode() + b"\n",
            canonical.replace(
                b'"schema_version":1',
                b'"schema_version":1,"schema_version":1',
                1,
            ),
        )
        for raw in variants:
            with self.subTest(raw=raw[:40]):
                self.provider.write(raw)
                self.assert_rejected("canonical|duplicate")

    def test_rejects_missing_extra_and_nondigest_top_level_schema(self) -> None:
        variants = []
        missing = copy.deepcopy(self.provider.value)
        del missing["authority_profile"]
        variants.append(missing)
        extra = copy.deepcopy(self.provider.value)
        extra["unexpected"] = True
        variants.append(extra)
        nondigest = copy.deepcopy(self.provider.value)
        nondigest["content_sha256"] = "not-a-digest"
        variants.append(nondigest)
        wrong_contract = copy.deepcopy(self.provider.value)
        wrong_contract["contract"] = PROVIDER_CONTRACT + "-other"
        variants.append(wrong_contract)
        for value in variants:
            with self.subTest(keys=sorted(value)):
                self.provider.value = value
                self.provider.write()
                self.assert_rejected("schema|digest|contract")

    def test_rejects_invalid_tokens_capabilities_and_lifecycle(self) -> None:
        variants = []
        uppercase = copy.deepcopy(self.provider.value)
        uppercase["plugin_id"] = "Demo"
        variants.append(uppercase)
        capability = copy.deepcopy(self.provider.value)
        capability["issuers"][0]["capabilities"] = ["not/a/token"]
        variants.append(capability)
        lifecycle = copy.deepcopy(self.provider.value)
        lifecycle["validators"][0]["lifecycle"] = {
            "state": "historical-usable",
            "usable_for_new_publication": True,
        }
        variants.append(lifecycle)
        lifecycle_extra = copy.deepcopy(self.provider.value)
        lifecycle_extra["producers"][0]["lifecycle"]["extra"] = False
        variants.append(lifecycle_extra)
        for value in variants:
            with self.subTest(value=value):
                self.provider.value = value
                self.provider.refresh_content_digest()
                self.provider.write()
                self.assert_rejected("token|lifecycle|active")

    def test_rejects_entrypoint_that_is_not_the_first_module(self) -> None:
        validator = self.provider.value["validators"][0]
        validator["modules"].reverse()
        self.provider.refresh_validator_identity()
        self.provider.write()

        self.assert_rejected("entrypoint.*first")

    def test_validator_aggregate_budget_accepts_exact_and_rejects_one_over(
        self,
    ) -> None:
        deployment = self.deployment()
        exact = ProviderFixture(self.root / "exact-budget", prefix="exact-budget")
        self.resize_validator_modules(
            exact,
            deployment.MAX_VALIDATOR_ARTIFACT_BYTES,
        )

        accepted = self.materialize(exact, self.root / "exact-budget-trust")

        self.assertEqual(
            sum(len(module.raw) for module in accepted.modules),
            deployment.MAX_VALIDATOR_ARTIFACT_BYTES,
        )
        over = ProviderFixture(self.root / "over-budget", prefix="over-budget")
        self.resize_validator_modules(
            over,
            deployment.MAX_VALIDATOR_ARTIFACT_BYTES + 1,
        )
        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "aggregate.*limit",
        ):
            self.materialize(over, self.root / "over-budget-trust")

    def test_controller_budgets_match_the_existing_consumer(self) -> None:
        deployment = self.deployment()

        self.assertEqual(
            deployment.MAX_VALIDATOR_ARTIFACT_BYTES,
            self.integer_source_constant(
                "runtime/trust.py",
                "MAX_VALIDATOR_ARTIFACT_BYTES",
            ),
        )
        self.assertEqual(
            deployment.MAX_TRUST_CONTEXT_BYTES,
            self.integer_source_constant("runtime/canonical.py", "MAX_JSON_BYTES"),
        )
        self.assertEqual(
            deployment.MAX_TRUST_CONTEXT_BYTES,
            self.integer_source_constant("runtime/bundle_io.py", "MAX_FILE_BYTES"),
        )
        self.assertEqual(
            deployment.MAX_TRUST_CONTEXT_BYTES,
            self.integer_source_constant(
                "client/task_witness_client.py",
                "MAX_DOCUMENT_BYTES",
            ),
        )

    def test_rejects_duplicate_module_names_and_paths(self) -> None:
        for field in ("name", "relative_path"):
            with self.subTest(field=field):
                self.provider.value = self.provider.build_value()
                modules = self.provider.validator_modules
                modules[1][field] = modules[0][field]
                self.provider.refresh_validator_identity()
                self.provider.write()
                self.assert_rejected("duplicate module")

    def test_rejects_unsorted_capabilities_and_role_inventories(self) -> None:
        capability = copy.deepcopy(self.provider.value)
        capability["issuers"][0]["capabilities"] = ["z-last", "a-first"]
        capability["content_sha256"] = sha256(
            canonical_bytes(
                {key: value for key, value in capability.items() if key != "content_sha256"}
            )
        )
        self.provider.value = capability
        self.provider.write()
        self.assert_rejected("sorted")

        self.provider.value = self.provider.build_value()
        extra = copy.deepcopy(self.provider.value["producers"][0])
        extra["producer_id"] = "aaa-producer"
        extra["implementation_sha256"] = sha256(b"aaa-producer")
        self.provider.value["producers"].append(extra)
        self.provider.refresh_content_digest()
        self.provider.write()
        self.assert_rejected("sorted")

    def test_rejects_validator_implementation_identity_disagreement(self) -> None:
        self.provider.value["validators"][0]["implementation_sha256"] = "0" * 64
        self.provider.value["producers"][0]["validator_implementation_sha256"] = (
            "0" * 64
        )
        self.provider.refresh_content_digest()
        self.provider.write()

        self.assert_rejected("implementation.*identity")

    def test_rejects_unsafe_module_paths(self) -> None:
        paths = ("", "/absolute.py", "./validator.py", "../validator.py", "a/../b.py", "a\\b.py")
        for relative in paths:
            with self.subTest(relative=relative):
                value = self.provider.build_value()
                value["validators"][0]["modules"][0]["relative_path"] = relative
                self.provider.value = value
                self.provider.refresh_validator_identity()
                self.provider.write()
                self.assert_rejected("relative path|path component")

    def test_rejects_module_length_disagreement(self) -> None:
        self.provider.validator_modules[0]["length"] += 1
        self.provider.refresh_content_digest()
        self.provider.write()

        self.assert_rejected("length")

    def test_rejects_module_content_digest_disagreement(self) -> None:
        self.provider.validator_modules[0]["sha256"] = "0" * 64
        self.provider.refresh_validator_identity()
        self.provider.write()

        self.assert_rejected("digest|sha256")

    def test_rejects_intermediate_symlink_in_module_path(self) -> None:
        real = self.provider.root / "real"
        real.mkdir()
        source = real / "validator.py"
        source.write_bytes(self.provider.entrypoint.read_bytes())
        os.symlink(real, self.provider.root / "linked")
        module = self.provider.validator_modules[0]
        module.update(
            relative_path="linked/validator.py",
            length=len(source.read_bytes()),
            sha256=sha256(source.read_bytes()),
        )
        self.provider.refresh_validator_identity()
        self.provider.write()

        self.assert_rejected("symlink|directory")

    def test_rejects_final_symlink_module(self) -> None:
        link = self.provider.module_root / "linked.py"
        os.symlink(self.provider.entrypoint.name, link)
        module = self.provider.validator_modules[0]
        module.update(
            relative_path="validators/linked.py",
            length=len(self.provider.entrypoint.read_bytes()),
            sha256=sha256(self.provider.entrypoint.read_bytes()),
        )
        self.provider.refresh_validator_identity()
        self.provider.write()

        self.assert_rejected("symlink|regular file")

    def test_rejects_special_file_module(self) -> None:
        fifo = self.provider.module_root / "validator.fifo"
        os.mkfifo(fifo)
        self.provider.validator_modules[0]["relative_path"] = "validators/validator.fifo"
        self.provider.refresh_validator_identity()
        self.provider.write()

        self.assert_rejected("regular file|special")

    def test_ignores_undeclared_sibling_files(self) -> None:
        (self.provider.module_root / "undeclared.py").write_text(
            "UNDECLARED = True\n", encoding="utf-8"
        )

        result = self.materialize()

        self.assertEqual([item.name for item in result.modules], ["validator", "helper"])
        self.assertFalse(any(item.path.name == "undeclared.py" for item in result.modules))

    def test_rewrites_retained_paths_and_returns_exact_immutable_inventory(self) -> None:
        result = self.materialize()
        generation = self.trust / "validators" / f"sha256-{self.provider.implementation_sha256}"

        self.assertEqual(
            {path.name for path in generation.iterdir()}, {"validator.py", "helper.py"}
        )
        for item, source in zip(result.modules, (self.provider.entrypoint, self.provider.helper)):
            self.assertEqual(item.path, (generation / f"{item.name}.py").resolve())
            self.assertEqual(item.raw, source.read_bytes())
            self.assertEqual(item.sha256, sha256(item.raw))
            assert_private_regular(self, item.path)
        self.assertIsInstance(result.validators[0], MappingProxyType)
        with self.assertRaises(TypeError):
            result.validators[0]["contract"] = "changed"

    def test_rechecks_source_path_mapping_after_retention(self) -> None:
        deployment = self.deployment()
        original = deployment._materialized_provider

        def replace_source(*args, **kwargs):
            result = original(*args, **kwargs)
            replacement = self.provider.entrypoint.with_suffix(".replacement")
            replacement.write_bytes(self.provider.entrypoint.read_bytes())
            os.replace(replacement, self.provider.entrypoint)
            return result

        with mock.patch.object(
            deployment,
            "_materialized_provider",
            side_effect=replace_source,
        ), self.assertRaisesRegex(deployment.DeploymentError, "changed|mapping"):
            deployment.materialize_provider(self.provider.root, self.trust)

    def test_accepts_a_hardlinked_manager_owned_source_file(self) -> None:
        source_alias = self.root / "manager-cache-validator.py"
        os.link(self.provider.entrypoint, source_alias)

        result = self.materialize()

        self.assertEqual(result.modules[0].raw, self.provider.entrypoint.read_bytes())
        assert_private_regular(self, result.modules[0].path)
        self.assertEqual(result.modules[0].path.stat().st_nlink, 1)

    def test_source_root_does_not_require_effective_user_ownership(self) -> None:
        deployment = self.deployment()
        with mock.patch.object(
            deployment.os,
            "geteuid",
            return_value=os.geteuid() + 1,
        ):
            snapshot = deployment._open_root(
                self.provider.root,
                "verified manager plugin root",
            )
        os.close(snapshot.fd)

    def test_distinct_validators_may_share_one_retained_implementation(self) -> None:
        shared = copy.deepcopy(self.provider.value["validators"][0])
        shared["validator_id"] = "demo-validator-alias"
        self.provider.value["validators"].append(shared)
        self.provider.value["validators"].sort(key=lambda item: item["validator_id"])
        self.provider.refresh_content_digest()
        self.provider.write()

        result = self.materialize()

        self.assertEqual(len(result.validators), 2)
        self.assertEqual([item.name for item in result.modules], ["validator", "helper"])

    def test_accepts_an_identical_existing_validator_generation(self) -> None:
        first = self.materialize()
        second = self.materialize()

        self.assertEqual(first, second)

    def test_rejects_changed_bytes_under_an_existing_validator_identity(self) -> None:
        result = self.materialize()
        result.modules[0].path.write_bytes(b"changed\n")

        self.assert_rejected("retained.*bytes|validator generation")

    def test_rejects_extra_inventory_under_an_existing_validator_identity(self) -> None:
        result = self.materialize()
        extra = result.modules[0].path.parent / "extra.py"
        extra.write_text("EXTRA = True\n", encoding="utf-8")

        self.assert_rejected("inventory|validator generation")

    def test_composition_is_deterministic_across_provider_input_order(self) -> None:
        second = ProviderFixture(self.root / "second", prefix="second")
        first_provider = self.materialize()
        second_provider = self.materialize(second)
        first = self.compose([first_provider, second_provider])
        second_order = self.compose([second_provider, first_provider])

        self.assertEqual(first.raw, second_order.raw)
        self.assertEqual(first.sha256, second_order.sha256)
        self.assertEqual(first.value, second_order.value)

    def test_composition_uses_only_retained_bytes_after_import(self) -> None:
        provider = self.materialize()
        shutil.rmtree(self.provider.root)

        result = self.compose([provider])

        self.assertTrue(result.path.is_file())
        self.assertTrue(
            all(self.trust in module.path.parents for module in provider.modules)
        )

    def test_retained_paths_resolve_a_symlinked_trust_root_ancestor(self) -> None:
        first_parent = self.root / "first-install"
        second_parent = self.root / "second-install"
        first_parent.mkdir()
        second_parent.mkdir()
        alias = self.root / "installation-alias"
        alias.symlink_to(first_parent, target_is_directory=True)
        aliased_trust = alias / "trust"

        provider = self.materialize(trust=aliased_trust)
        result = self.compose([provider], aliased_trust)
        expected_root = first_parent / "trust"

        self.assertEqual(result.path.parent.parent, expected_root)
        self.assertTrue(
            all(expected_root in module.path.parents for module in provider.modules)
        )
        alias.unlink()
        alias.symlink_to(second_parent, target_is_directory=True)
        self.assertEqual(result.path.read_bytes(), result.raw)
        self.assertTrue(all(module.path.is_file() for module in provider.modules))

    def test_staged_trust_projects_installed_paths_without_rewriting_bytes(self) -> None:
        deployment = self.deployment()
        storage_trust = self.root / "staging" / "trust"
        installed_trust = self.root / "installed" / "trust"
        storage_trust.parent.mkdir(mode=0o700)
        provider = deployment.materialize_provider(
            self.provider.root,
            storage_trust,
            installed_trust_root=installed_trust,
        )
        deployment.materialize_intrinsic_smoke_provider(
            storage_trust,
            installed_trust_root=installed_trust,
        )
        staged = deployment.materialize_trust_context(
            [provider],
            storage_trust,
            installed_trust_root=installed_trust,
        )
        verified = deployment.compose_trust_context(
            [provider],
            storage_trust,
            installed_trust_root=installed_trust,
        )

        self.assertEqual(staged, verified)
        self.assertTrue(staged.storage_path.is_file())
        self.assertFalse(staged.path.exists())
        self.assertEqual(staged.path.parent.parent, installed_trust)
        for module in provider.modules:
            self.assertTrue(module.storage_path.is_file())
            self.assertFalse(module.path.exists())
            self.assertEqual(module.path.parents[2], installed_trust)
        self.assertTrue(
            all(
                installed_trust in Path(module["path"]).parents
                for validator in staged.value["validators"]
                for module in validator["modules"]
            )
        )

    def test_rejects_retained_root_canonical_mapping_disagreement(self) -> None:
        deployment = self.deployment()
        self.trust.mkdir(mode=0o700)
        disagreement = self.root / "different-private-root"
        disagreement.mkdir(mode=0o700)

        with mock.patch.object(
            deployment.Path,
            "resolve",
            return_value=disagreement,
        ), self.assertRaisesRegex(
            deployment.DeploymentError,
            "path mapping changed",
        ):
            deployment._open_private_root(self.trust, create=False)

    def test_composition_includes_the_intrinsic_smoke_provider(self) -> None:
        result = self.compose()

        self.assertTrue(SMOKE_VALIDATOR_SOURCE.is_file())
        self.assertTrue(result.value["producers"])
        self.assertTrue(result.value["validators"])
        self.assertTrue(
            any(
                item["modules"][0]["path"]
                == str((self.trust / "validators" / f"sha256-{item['implementation_sha256']}" / f"{item['entrypoint']}.py").resolve())
                for item in result.value["validators"]
            )
        )

    def test_composition_rejects_an_external_collision_with_smoke(self) -> None:
        smoke = self.compose()
        smoke_producer = smoke.value["producers"][0]
        collision = ProviderFixture(self.root / "collision", prefix="collision")
        collision.value["producers"][0].update(
            producer_id=smoke_producer["producer_id"],
            contract=smoke_producer["contract"],
            implementation_sha256=smoke_producer["implementation_sha256"],
        )
        collision.refresh_content_digest()
        collision.write()
        provider = self.materialize(collision)

        with self.assertRaisesRegex(
            self.deployment().DeploymentError, "duplicate|conflicting"
        ):
            self.compose([provider])

    def test_composition_rejects_duplicate_external_identities(self) -> None:
        first = self.materialize()
        duplicate_root = self.root / "duplicate"
        duplicate = self.provider.clone_to(duplicate_root)
        second = self.materialize(duplicate)

        with self.assertRaisesRegex(
            self.deployment().DeploymentError, "duplicate|conflicting"
        ):
            self.compose([first, second])

    def test_composition_rejects_an_unregistered_producer_validator(self) -> None:
        self.provider.value["producers"][0]["validator_id"] = "missing-validator"
        self.provider.refresh_content_digest()
        self.provider.write()
        provider = self.materialize()

        with self.assertRaisesRegex(
            self.deployment().DeploymentError, "unregistered validator"
        ):
            self.compose([provider])

    def test_composition_does_not_recreate_a_missing_retained_generation(self) -> None:
        provider = self.materialize()
        self.compose([provider])
        generation = provider.modules[0].path.parent
        shutil.rmtree(generation)

        with self.assertRaisesRegex(
            self.deployment().DeploymentError, "generation|missing"
        ):
            self.deployment().compose_trust_context([provider], self.trust)

        self.assertFalse(generation.exists())

    def test_composition_does_not_recreate_a_missing_smoke_generation(self) -> None:
        context = self.compose()
        generation = Path(context.value["validators"][0]["modules"][0]["path"]).parent
        shutil.rmtree(generation)

        with self.assertRaisesRegex(
            self.deployment().DeploymentError, "generation|missing"
        ):
            self.deployment().compose_trust_context([], self.trust)

        self.assertFalse(generation.exists())

    def test_context_is_compatible_with_task_witness_trust_context_v2(self) -> None:
        provider = self.materialize()
        result = self.compose([provider])

        self.assertIsInstance(result, self.deployment().TrustContextMaterialization)
        self.assertEqual(result.raw, canonical_document(result.value))
        self.assertEqual(result.sha256, sha256(result.raw))
        self.assertEqual(
            result.path,
            (
                self.trust
                / "contexts"
                / f"sha256-{result.sha256}.json"
            ).resolve(),
        )
        self.assertEqual(result.value["schema_version"], 1)
        self.assertEqual(result.value["contract"], TRUST_CONTRACT)
        unsigned = {
            key: value
            for key, value in result.value.items()
            if key != "content_sha256"
        }
        self.assertEqual(result.value["content_sha256"], sha256(canonical_bytes(unsigned)))
        for category in ("producers", "issuers", "validators"):
            for entry in result.value[category]:
                self.assertNotIn("lifecycle", entry)
                self.assertEqual(entry["state"], "active")
                self.assertIs(entry["usable_for_new_publication"], True)
        with self.assertRaises(TypeError):
            result.value["contract"] = "changed"
        with self.assertRaises(TypeError):
            result.value["producers"][0]["state"] = "revoked"

    def test_trust_context_budget_accepts_exact_and_rejects_one_over(self) -> None:
        deployment = self.deployment()
        unsigned = {
            "schema_version": 1,
            "contract": TRUST_CONTRACT,
            "producers": [],
            "issuers": [{"padding": ""}],
            "validators": [],
        }
        _, baseline = deployment._bounded_trust_context_document(unsigned)
        padding = deployment.MAX_TRUST_CONTEXT_BYTES - len(baseline)
        unsigned["issuers"][0]["padding"] = "x" * padding

        _, exact = deployment._bounded_trust_context_document(unsigned)

        self.assertEqual(len(exact), deployment.MAX_TRUST_CONTEXT_BYTES)
        unsigned["issuers"][0]["padding"] += "x"
        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "trust context.*limit",
        ):
            deployment._bounded_trust_context_document(unsigned)

    def test_oversized_composed_context_is_not_published(self) -> None:
        providers = []
        for prefix in ("large-first", "large-second"):
            fixture = ProviderFixture(self.root / prefix, prefix=prefix)
            fixture.value["producers"] = []
            fixture.value["validators"] = []
            lifecycle = {"state": "active", "usable_for_new_publication": True}
            fixture.value["issuers"] = [
                {
                    "issuer_id": f"{prefix}-issuer-{index:04d}",
                    "contract": "x" * 900,
                    "implementation_sha256": sha256(
                        f"{prefix}-{index}".encode()
                    ),
                    "capabilities": ["operator-choice"],
                    "lifecycle": lifecycle,
                }
                for index in range(500)
            ]
            fixture.refresh_content_digest()
            fixture.write()
            providers.append(self.materialize(fixture))

        with self.assertRaisesRegex(
            self.deployment().DeploymentError,
            "trust context.*limit",
        ):
            self.compose(providers)

        self.assertFalse((self.trust / "contexts").exists())

    def test_accepts_an_identical_existing_context(self) -> None:
        provider = self.materialize()
        first = self.compose([provider])
        second = self.deployment().compose_trust_context([provider], self.trust)

        self.assertEqual(first, second)
        assert_private_regular(self, first.path)

    def test_composition_does_not_recreate_a_missing_context(self) -> None:
        provider = self.materialize()
        context = self.compose([provider])
        context.path.unlink()

        with self.assertRaisesRegex(
            self.deployment().DeploymentError, "context|missing"
        ):
            self.deployment().compose_trust_context([provider], self.trust)

        self.assertFalse(context.path.exists())

    def test_composition_does_not_create_a_missing_context_store(self) -> None:
        deployment = self.deployment()
        deployment.materialize_intrinsic_smoke_provider(self.trust)

        with self.assertRaisesRegex(deployment.DeploymentError, "contexts"):
            deployment.compose_trust_context([], self.trust)

        self.assertFalse((self.trust / "contexts").exists())

    def test_rejects_changed_bytes_under_an_existing_context_identity(self) -> None:
        provider = self.materialize()
        context = self.compose([provider])
        context.path.write_bytes(b"{}\n")

        with self.assertRaisesRegex(
            self.deployment().DeploymentError, "retained.*context|context.*bytes"
        ):
            self.deployment().compose_trust_context([provider], self.trust)

    def test_validator_identity_fixture_uses_the_existing_path_independent_frame(self) -> None:
        modules = self.provider.validator_modules
        expected = validator_identity(
            "demo-bundle-v1",
            "validator",
            [(item["name"], item["sha256"]) for item in modules],
        )

        self.assertEqual(self.provider.implementation_sha256, expected)
        changed_paths = copy.deepcopy(modules)
        changed_paths[0]["relative_path"] = "other/location.py"
        self.assertEqual(
            validator_identity(
                "demo-bundle-v1",
                "validator",
                [(item["name"], item["sha256"]) for item in changed_paths],
            ),
            expected,
        )


if __name__ == "__main__":
    unittest.main()
