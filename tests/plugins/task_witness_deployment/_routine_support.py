from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from unittest import mock

from ._activation_support import (
    FirstInstallActivationFixture,
    PreparedActivation,
    SmokeChildBoundary,
    expected_smoke_envelope,
)
from ._support import (
    ProviderFixture,
    adapt_agent_plugins_candidate,
    canonical_document,
    content_document,
    set_agent_plugins_candidate_version,
    sha256,
    validator_identity,
)

FREEZE5_COMMIT = "96608a9b91d4dcf3f468a4fab1f0e008c9c32b36"
FREEZE5_PLUGIN_PREFIX = "plugins/task-witness/"


def _freeze5_manifest_raw(relative_path: str) -> bytes:
    """Load one exact legacy-manifest authority from the immutable F5 object."""

    repository = Path(__file__).resolve().parents[3]
    resolved = (
        subprocess.run(
            ("git", "rev-parse", "--verify", f"{FREEZE5_COMMIT}^{{commit}}"),
            cwd=repository,
            check=True,
            capture_output=True,
        )
        .stdout.decode("ascii")
        .strip()
    )
    if resolved != FREEZE5_COMMIT:
        raise AssertionError("Freeze 5 commit identity disagrees")
    return subprocess.run(
        (
            "git",
            "show",
            f"{FREEZE5_COMMIT}:{FREEZE5_PLUGIN_PREFIX}{relative_path}",
        ),
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout


def smoke_envelope(smoke: object) -> bytes:
    value = json.loads(json.dumps(smoke))
    envelope = {
        "contract": "task-witness-launch-envelope-v1",
        "anchor": value["expected_anchor"],
        "witness": {
            "contract": "task-witness-canonical-projection-v2",
            "bundle_sha256": value["bundle"]["sha256"],
            "producer": value["producer"],
            "validator": value["validator"],
            "projection": value["expected_projection"],
            "trust_context_sha256": value["trust_context"]["sha256"],
            "historical": False,
        },
    }
    raw = canonical_document(envelope)
    if sha256(raw) != value["expected_envelope_sha256"]:
        raise AssertionError("routine smoke envelope binding disagrees")
    return raw


class RoutineSmokeBoundary:
    """Return phase-specific candidate/prior smoke at the process boundary."""

    def __init__(
        self,
        canonical_root: Path,
        candidate_smoke: object,
        prior_smoke: object,
        *,
        candidate_accepted: bool,
        rollback_accepted: bool,
    ) -> None:
        self.canonical_root = canonical_root
        self.outputs = {
            "candidate-smoke": smoke_envelope(candidate_smoke),
            "rollback-smoke": smoke_envelope(prior_smoke),
        }
        self.accepted = {
            "candidate-smoke": candidate_accepted,
            "rollback-smoke": rollback_accepted,
        }
        self.calls: list[str] = []

    def __call__(self, argv: tuple[str, ...], *, pass_fds: tuple[int, ...]):
        if argv != (str(self.canonical_root / "task-witness"), "activation-smoke"):
            raise AssertionError("routine smoke argv disagrees")
        if pass_fds != (3,):
            raise AssertionError("routine smoke descriptor set disagrees")
        journal = json.loads((self.canonical_root / "transaction.json").read_bytes())
        phase = journal["phase"]
        if phase not in self.outputs:
            raise AssertionError(f"routine smoke phase disagrees: {phase}")
        self.calls.append(phase)
        accepted = self.accepted[phase]
        return subprocess.CompletedProcess(
            argv,
            0 if accepted else 70,
            stdout=self.outputs[phase] if accepted else b"",
            stderr=b"" if accepted else b"rejected\n",
        )


class RoutineDeploymentFixture:
    """Build exact public A -> B inputs without bypassing activation."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.first_install = FirstInstallActivationFixture(root / "initial")

    def deployment(self):
        return self.first_install.deployment()

    def activate_initial(
        self,
        candidate_root: Path | None = None,
        *,
        source_identity: dict[str, object] | None = None,
    ):
        deployment = self.deployment()
        if candidate_root is None:
            prepared = self.first_install.prepare()
        else:
            account_home = self.first_install.root / "account-home"
            canonical_root = account_home / ".local" / "libexec" / "task-witness"
            canonical_root.mkdir(parents=True, mode=0o700)
            for directory in (
                account_home,
                account_home / ".local",
                account_home / ".local" / "libexec",
                canonical_root,
            ):
                directory.chmod(0o700)
            activation_lock = canonical_root / "activation.lock"
            activation_lock.write_bytes(b"")
            activation_lock.chmod(0o600)
            if source_identity is None:
                request = self.first_install.first_install_request(
                    canonical_root,
                    candidate_root=candidate_root,
                )
            else:
                routine_request = self.request_for_candidate(
                    canonical_root,
                    "0" * 64,
                    candidate_root,
                    release_version="1.0.0",
                    revision="a" * 40,
                    sequence=7,
                    **source_identity,
                )
                request = deployment.FirstInstallRequest(
                    candidate_root=routine_request.candidate_root,
                    canonical_root=routine_request.canonical_root,
                    source_selection_raw=routine_request.source_selection_raw,
                    source_evidence=routine_request.source_evidence,
                    runtime_qualification_raw=routine_request.runtime_qualification_raw,
                    maintenance_transaction_sha256="9" * 64,
                )
            first = deployment.prepare_first_install(request)
            authorization_raw = self.first_install.first_install_authorization_raw(
                first
            )
            staged = deployment.stage_first_install(
                request,
                authorization_raw,
                self.first_install.root / "stage",
            )
            prepared = PreparedActivation(
                request=request,
                authorization_raw=authorization_raw,
                staged=staged,
                verified=deployment.verify_deployment_stage(staged.stage_path),
                canonical_root=canonical_root,
                activation_lock=activation_lock,
            )
        request = self.first_install.activation_request(prepared)
        smoke = SmokeChildBoundary(
            prepared,
            expected_smoke_envelope(prepared.staged),
        )
        with mock.patch.object(
            deployment,
            "_spawn_activation_smoke_child",
            smoke,
        ):
            result = deployment.activate_staged(request)
        return prepared, result

    def candidate_root(self, *, legacy_manifest: bool = False) -> Path:
        destination = self.root / "candidate-b"
        shutil.copytree(
            Path(__file__).resolve().parents[3] / "plugins" / "task-witness",
            destination,
        )
        runtime = destination / "runtime" / "task_witness.py"
        runtime.write_bytes(runtime.read_bytes() + b"\n# routine candidate B\n")
        if legacy_manifest:
            (destination / "plugin.json").unlink()
            for relative in (
                ".claude-plugin/plugin.json",
                ".codex-plugin/plugin.json",
            ):
                manifest = destination / relative
                manifest.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                value = json.loads(_freeze5_manifest_raw(relative))
                value["version"] = "0.1.1"
                manifest.write_text(
                    json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            if (destination / "plugin.json").exists() or not all(
                (destination / relative).is_file()
                for relative in (
                    ".claude-plugin/plugin.json",
                    ".codex-plugin/plugin.json",
                )
            ):
                raise AssertionError("bridge legacy manifest epoch disagrees")
        else:
            adapt_agent_plugins_candidate(destination, version="1.0.1")
        return destination

    def provider_candidate_pair(self) -> tuple[Path, Path, dict[str, object]]:
        source = Path(__file__).resolve().parents[3] / "plugins" / "task-witness"
        candidate_a = self.root / "provider-candidate-a"
        shutil.copytree(source, candidate_a)
        generated = ProviderFixture(self.root / "generated-provider")
        value = generated.value
        value = content_document(
            {key: item for key, item in value.items() if key != "content_sha256"}
        )
        (candidate_a / "task-witness-provider.json").write_bytes(
            canonical_document(value)
        )
        shutil.copytree(generated.module_root, candidate_a / "validators")
        manifest_author = {
            "name": "Demo Publisher",
            "url": "https://github.com/demo-publisher",
        }
        manifest = candidate_a / "plugin.json"
        manifest_value = json.loads(manifest.read_text(encoding="utf-8"))
        manifest_value["name"] = value["plugin_id"]
        manifest_value["author"] = manifest_author
        manifest_value["repository"] = value["repository"]
        manifest_value["homepage"] = f"{value['repository']}/tree/main/plugin"
        manifest.write_text(
            json.dumps(manifest_value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        adapt_agent_plugins_candidate(candidate_a)
        policy_path = candidate_a / "controller" / "policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["source"] = {
            "plugin_id": value["plugin_id"],
            "mode": "harness_snapshot",
            "publisher_id": value["publisher"],
            "manifest_author": manifest_author,
            "repository_id": "demo/provider",
            "repository_url": value["repository"],
            "source_authority": "github-demo-provider",
            "details": {
                "channel": "stable",
                "trust_class": "operator-installed",
                "lineage_id": "agents-stable",
            },
        }
        lifecycle = {"state": "active", "usable_for_new_publication": True}
        policy["providers"] = [
            {
                "plugin_id": value["plugin_id"],
                "authority_profile": value["authority_profile"],
                "producers": [
                    {
                        "producer_id": item["producer_id"],
                        "contract": item["contract"],
                        "validator_id": item["validator_id"],
                        "validator_contract": item["validator_contract"],
                        **lifecycle,
                    }
                    for item in value["producers"]
                ],
                "issuers": [
                    {
                        "issuer_id": item["issuer_id"],
                        "contract": item["contract"],
                        "capabilities": item["capabilities"],
                        **lifecycle,
                    }
                    for item in value["issuers"]
                ],
                "validators": [
                    {
                        "validator_id": item["validator_id"],
                        "contract": item["contract"],
                        **lifecycle,
                    }
                    for item in value["validators"]
                ],
            }
        ]
        policy_path.write_bytes(
            canonical_document(
                content_document(
                    {
                        key: item
                        for key, item in policy.items()
                        if key != "content_sha256"
                    }
                )
            )
        )

        candidate_b = self.root / "provider-candidate-b"
        shutil.copytree(candidate_a, candidate_b)
        runtime = candidate_b / "runtime" / "task_witness.py"
        runtime.write_bytes(
            runtime.read_bytes() + b"\n# routine provider candidate B\n"
        )
        set_agent_plugins_candidate_version(candidate_b, "1.0.1")
        helper = candidate_b / "validators" / "helper.py"
        helper.write_bytes(helper.read_bytes() + b"VALUE_2 = 'compatible'\n")
        declaration_path = candidate_b / "task-witness-provider.json"
        declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
        validator = declaration["validators"][0]
        for module in validator["modules"]:
            raw = (candidate_b / module["relative_path"]).read_bytes()
            module["length"] = len(raw)
            module["sha256"] = sha256(raw)
        implementation = validator_identity(
            validator["contract"],
            validator["entrypoint"],
            [(item["name"], item["sha256"]) for item in validator["modules"]],
        )
        validator["implementation_sha256"] = implementation
        declaration["producers"][0]["validator_implementation_sha256"] = implementation
        declaration_path.write_bytes(
            canonical_document(
                content_document(
                    {
                        key: item
                        for key, item in declaration.items()
                        if key != "content_sha256"
                    }
                )
            )
        )
        identity: dict[str, object] = {
            "plugin_id": value["plugin_id"],
            "publisher_id": value["publisher"],
            "manifest_author": manifest_author,
            "repository_id": "demo/provider",
            "repository_url": value["repository"],
            "source_authority": "github-demo-provider",
        }
        return candidate_a, candidate_b, identity

    def request(self, canonical_root: Path, active_sha256: str):
        candidate_root = self.candidate_root()
        return self.request_for_candidate(
            canonical_root,
            active_sha256,
            candidate_root,
            release_version="1.0.1",
            revision="b" * 40,
            sequence=8,
        )

    def request_for_candidate(
        self,
        canonical_root: Path,
        active_sha256: str,
        candidate_root: Path,
        *,
        release_version: str,
        revision: str,
        sequence: int,
        plugin_id: str = "task-witness",
        publisher_id: str = "nisavid",
        manifest_author: dict[str, str] | None = None,
        repository_id: str = "nisavid/agents",
        repository_url: str = "https://github.com/nisavid/agents",
        source_authority: str = "github-nisavid-agents",
    ):
        deployment = self.deployment()
        snapshot = deployment._snapshot_candidate_tree(candidate_root)
        receipt = b"opaque routine Task Witness manager receipt\n"
        lineage = {"lineage_id": "agents-stable", "sequence": sequence}
        shared = {
            "plugin_id": plugin_id,
            "release_version": release_version,
            "revision": revision,
            "subtree_sha256": snapshot.subtree_sha256,
            "channel": "stable",
            "manager_trust_class": "operator-installed",
            "source_authority": source_authority,
            "lineage": lineage,
        }
        selection_raw = canonical_document(
            content_document(
                {
                    "schema_version": 1,
                    "contract": "task-witness-source-selection-v1",
                    "mode": "harness_snapshot",
                    "publisher_id": publisher_id,
                    "manifest_author": manifest_author
                    or {
                        "name": "Ivan D Vasin",
                        "url": "https://github.com/nisavid",
                    },
                    "repository_id": repository_id,
                    "repository_url": repository_url,
                    "release_version": shared["release_version"],
                    "revision": shared["revision"],
                    "subtree_sha256": shared["subtree_sha256"],
                    "source_authority": shared["source_authority"],
                    "details": {
                        "harness": "codex",
                        "manager": "codex-plugin-manager",
                        "channel": shared["channel"],
                        "manager_trust_class": shared["manager_trust_class"],
                        "manager_receipt_sha256": sha256(receipt),
                        "lineage": lineage,
                    },
                }
            )
        )
        binding_raw = canonical_document(
            content_document(
                {
                    "schema_version": 1,
                    "contract": "task-witness-manager-binding-v1",
                    "harness": "codex",
                    "manager": "codex-plugin-manager",
                    "adapter_sha256": sha256(b"exact private harness adapter"),
                    "manager_receipt_sha256": sha256(receipt),
                    "claims": shared,
                }
            )
        )
        return deployment.DeploymentRequest(
            candidate_root=candidate_root,
            canonical_root=canonical_root,
            source_selection_raw=selection_raw,
            source_evidence=deployment.HarnessSnapshotEvidence(
                binding_raw=binding_raw,
                receipt_raw=receipt,
            ),
            runtime_qualification_raw=self.first_install.runtime_qualification_raw(),
            maintenance_transaction_sha256="a" * 64,
            expected_active_receipt_sha256=active_sha256,
        )

    def authorization_raw(self, prepared: object) -> bytes:
        facts = prepared.authorization_facts
        return canonical_document(
            content_document(
                {
                    "schema_version": 1,
                    "contract": "task-witness-deployer-authorization-v1",
                    "purpose": "routine-compatible-forward",
                    "canonical_root": str(facts.canonical_root),
                    "effective_uid": facts.effective_uid,
                    "plan_sha256": facts.plan_sha256,
                    "maintenance_transaction_sha256": (
                        facts.maintenance_transaction_sha256
                    ),
                    "candidate_controller_sha256": (facts.candidate_controller_sha256),
                    "candidate_policy_sha256": facts.candidate_policy_sha256,
                    "source_selection_sha256": facts.source_selection_sha256,
                    "source_evidence_sha256": facts.source_evidence_sha256,
                    "expected_active_receipt_sha256": (
                        facts.expected_active_receipt_sha256
                    ),
                }
            )
        )

    def staged_routine(self):
        deployment = self.deployment()
        initial, active = self.activate_initial()
        request = self.request(initial.canonical_root, active.active_receipt_sha256)
        prepared = deployment.prepare_deployment(request)
        authorization_raw = self.authorization_raw(prepared)
        staged = deployment.stage_deployment(
            request,
            authorization_raw,
            self.root / "routine-stage",
        )
        activation = deployment.ActivationRequest(
            deployment=request,
            authorization_raw=authorization_raw,
            stage_receipt=staged.stage_path,
        )
        return initial, active, request, prepared, staged, activation

    @staticmethod
    def _binding(path: Path, raw: bytes, mode: int) -> dict[str, object]:
        return {
            "path": str(path),
            "length": len(raw),
            "sha256": sha256(raw),
            "owner": os.geteuid(),
            "mode": mode,
        }

    @staticmethod
    def _recontent(value: dict[str, object]) -> dict[str, object]:
        return content_document(
            {key: item for key, item in value.items() if key != "content_sha256"}
        )

    def rewrite_routine_stage(
        self,
        staged: object,
        mutator: Callable[
            [dict[str, object], dict[str, object], dict[str, object]],
            None,
        ],
    ) -> Path:
        """Coherently rehash a malformed inert stage through B and stage.json."""

        stage_path = Path(staged.stage_path)
        stage_root = stage_path.parent
        stage = json.loads(stage_path.read_bytes())

        def artifact(role: str) -> dict[str, object]:
            matches = [item for item in stage["artifacts"] if item["role"] == role]
            if len(matches) != 1:
                raise AssertionError(f"routine stage has ambiguous {role}")
            return matches[0]

        rollback_artifact = artifact("rollback-receipt")
        deployment_artifact = artifact("deployment-receipt")
        deployment_alias = artifact("deployment-alias")
        rollback_path = Path(rollback_artifact["staged"]["path"])
        deployment_path = Path(deployment_artifact["staged"]["path"])
        rollback = json.loads(rollback_path.read_bytes())
        deployment = json.loads(deployment_path.read_bytes())

        mutator(rollback, deployment, stage)

        rollback = self._recontent(rollback)
        rollback_raw = canonical_document(rollback)
        rollback_sha256 = sha256(rollback_raw)
        rollback_relative = f"receipts/sha256-{rollback_sha256}.json"
        rewritten_rollback_path = stage_root / rollback_relative
        rewritten_rollback_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if rollback_path != rewritten_rollback_path:
            rollback_path.replace(rewritten_rollback_path)
        rewritten_rollback_path.write_bytes(rollback_raw)
        rewritten_rollback_path.chmod(0o600)
        rollback_artifact.update(
            {
                "relative_path": rollback_relative,
                "staged": self._binding(rewritten_rollback_path, rollback_raw, 0o600),
                "installed": self._binding(
                    staged.plan.precondition.canonical_root
                    / "receipts"
                    / rewritten_rollback_path.name,
                    rollback_raw,
                    0o600,
                ),
            }
        )
        stage["rollback_receipt"] = {
            "path": rollback_artifact["installed"]["path"],
            "sha256": rollback_sha256,
        }

        deployment["rollback"] = {
            "state": "active",
            "path": rollback_artifact["installed"]["path"],
            "sha256": rollback_sha256,
        }
        deployment = self._recontent(deployment)
        deployment_raw = canonical_document(deployment)
        deployment_sha256 = sha256(deployment_raw)
        deployment_relative = f"receipts/sha256-{deployment_sha256}.json"
        rewritten_deployment_path = stage_root / deployment_relative
        if deployment_path != rewritten_deployment_path:
            deployment_path.replace(rewritten_deployment_path)
        rewritten_deployment_path.write_bytes(deployment_raw)
        rewritten_deployment_path.chmod(0o600)
        deployment_artifact.update(
            {
                "relative_path": deployment_relative,
                "staged": self._binding(
                    rewritten_deployment_path,
                    deployment_raw,
                    0o600,
                ),
                "installed": self._binding(
                    staged.plan.precondition.canonical_root
                    / "receipts"
                    / rewritten_deployment_path.name,
                    deployment_raw,
                    0o600,
                ),
            }
        )
        deployment_alias_path = Path(deployment_alias["staged"]["path"])
        deployment_alias_path.write_bytes(deployment_raw)
        deployment_alias_path.chmod(0o600)
        deployment_alias["staged"] = self._binding(
            deployment_alias_path,
            deployment_raw,
            0o600,
        )
        deployment_alias["installed"] = self._binding(
            staged.plan.precondition.canonical_root / "deployment.json",
            deployment_raw,
            0o600,
        )
        stage["deployment_receipt"] = {
            "path": deployment_artifact["installed"]["path"],
            "sha256": deployment_sha256,
        }
        stage["artifacts"].sort(key=lambda item: item["relative_path"])
        stage = self._recontent(stage)
        stage_path.write_bytes(canonical_document(stage))
        stage_path.chmod(0o600)
        return stage_path

    def rewrite_routine_stage_prior(
        self,
        staged: object,
        mutator: Callable[[dict[str, object], dict[str, object]], None],
    ) -> Path:
        """Coherently readdress the staged immediate-prior receipt and selector."""

        def mutate_outer(
            rollback: dict[str, object],
            deployment: dict[str, object],
            stage: dict[str, object],
        ) -> None:
            prior_artifact = next(
                item
                for item in stage["artifacts"]
                if item["role"] == "prior-deployment-alias"
            )
            active_artifact = next(
                item
                for item in stage["artifacts"]
                if item["role"] == "prior-active-record"
            )
            prior_path = Path(prior_artifact["staged"]["path"])
            active_path = Path(active_artifact["staged"]["path"])
            prior = json.loads(prior_path.read_bytes())
            active = json.loads(active_path.read_bytes())
            mutator(prior, active)

            active = self._recontent(active)
            active_raw = canonical_document(active)
            active_path.write_bytes(active_raw)
            active_path.chmod(0o600)
            active_staged = self._binding(active_path, active_raw, 0o600)
            active_installed = self._binding(
                Path(active_artifact["installed"]["path"]),
                active_raw,
                0o600,
            )
            active_artifact["staged"] = active_staged
            active_artifact["installed"] = active_installed

            active_sha256 = sha256(active_raw)
            runtime_sha256 = active["generation"].removeprefix("sha256-")
            prior["active"].update(
                {
                    "record_sha256": active_sha256,
                    "generation": active["generation"],
                    "runtime_contract": active["runtime_contract"],
                    "runtime_implementation_sha256": runtime_sha256,
                    "public_release": active["public_release"],
                }
            )
            anchor = prior["smoke"]["expected_anchor"]
            anchor.update(
                {
                    "active_record_sha256": active_sha256,
                    "generation": active["generation"],
                    "runtime_contract": active["runtime_contract"],
                    "runtime_implementation_sha256": runtime_sha256,
                    "public_release": active["public_release"],
                }
            )
            prior = self._recontent(prior)
            prior_raw = canonical_document(prior)
            prior_path.write_bytes(prior_raw)
            prior_path.chmod(0o600)
            prior_staged = self._binding(prior_path, prior_raw, 0o600)
            prior_installed = self._binding(
                Path(prior_artifact["installed"]["path"]),
                prior_raw,
                0o600,
            )
            prior_artifact["staged"] = prior_staged
            prior_artifact["installed"] = prior_installed

            prior_sha256 = sha256(prior_raw)
            rollback["precondition"]["active_receipt_sha256"] = prior_sha256
            rollback["prior_receipt"] = self._binding(
                Path(stage["canonical_root"])
                / "receipts"
                / f"sha256-{prior_sha256}.json",
                prior_raw,
                0o600,
            )
            prior_unit = rollback["prior_activation_unit"]
            prior_unit["active_record"] = active_installed
            prior_unit["deployment_receipt"] = prior_installed
            prior_unit["smoke"] = json.loads(json.dumps(prior["smoke"]))
            rollback["selector_preimage"] = [
                {
                    "role": "active-record",
                    "staged": active_staged,
                    "installed": active_installed,
                },
                {
                    "role": "deployment-alias",
                    "staged": prior_staged,
                    "installed": prior_installed,
                },
            ]
            rollback["smoke"] = json.loads(json.dumps(prior["smoke"]))
            deployment["prior_receipt_sha256"] = prior_sha256
            deployment["authorization"]["expected_active_receipt_sha256"] = prior_sha256

        return self.rewrite_routine_stage(staged, mutate_outer)

    def rewrite_routine_stage_candidate(
        self,
        staged: object,
        mutator: Callable[
            [dict[str, object], dict[str, object], dict[str, object]],
            None,
        ],
    ) -> Path:
        """Coherently readdress candidate B, its active record, and stage.json."""

        def mutate_outer(
            rollback: dict[str, object],
            deployment: dict[str, object],
            stage: dict[str, object],
        ) -> None:
            del rollback
            active_artifact = next(
                item for item in stage["artifacts"] if item["role"] == "active-record"
            )
            prior_artifact = next(
                item
                for item in stage["artifacts"]
                if item["role"] == "prior-deployment-alias"
            )
            active_path = Path(active_artifact["staged"]["path"])
            prior_path = Path(prior_artifact["staged"]["path"])
            active = json.loads(active_path.read_bytes())
            prior = json.loads(prior_path.read_bytes())
            mutator(deployment, active, prior)

            active = self._recontent(active)
            active_raw = canonical_document(active)
            active_path.write_bytes(active_raw)
            active_path.chmod(0o600)
            active_staged = self._binding(active_path, active_raw, 0o600)
            active_installed = self._binding(
                Path(active_artifact["installed"]["path"]),
                active_raw,
                0o600,
            )
            active_artifact["staged"] = active_staged
            active_artifact["installed"] = active_installed

            active_sha256 = sha256(active_raw)
            runtime_sha256 = active["generation"].removeprefix("sha256-")
            deployment["active"].update(
                {
                    "record_sha256": active_sha256,
                    "generation": active["generation"],
                    "runtime_contract": active["runtime_contract"],
                    "runtime_implementation_sha256": runtime_sha256,
                    "public_release": json.loads(json.dumps(active["public_release"])),
                }
            )
            smoke = deployment["smoke"]
            anchor = smoke["expected_anchor"]
            anchor.update(
                {
                    "active_record_sha256": active_sha256,
                    "generation": active["generation"],
                    "runtime_contract": active["runtime_contract"],
                    "interpreter": json.loads(json.dumps(active["interpreter"])),
                    "public_release": json.loads(json.dumps(active["public_release"])),
                    "runtime_implementation_sha256": runtime_sha256,
                }
            )
            envelope = {
                "contract": deployment["contracts"]["envelope"],
                "anchor": anchor,
                "witness": {
                    "contract": deployment["contracts"]["canonical_projection"],
                    "bundle_sha256": smoke["bundle"]["sha256"],
                    "producer": smoke["producer"],
                    "validator": smoke["validator"],
                    "projection": smoke["expected_projection"],
                    "trust_context_sha256": smoke["trust_context"]["sha256"],
                    "historical": False,
                },
            }
            smoke["expected_envelope_sha256"] = sha256(canonical_document(envelope))

        return self.rewrite_routine_stage(staged, mutate_outer)

    @staticmethod
    def stage_snapshot(stage_path: Path) -> dict[str, tuple[object, ...]]:
        root = stage_path.parent
        paths = [root, *sorted(root.rglob("*"))]
        snapshot: dict[str, tuple[object, ...]] = {}
        for path in paths:
            metadata = path.lstat()
            raw = path.read_bytes() if stat.S_ISREG(metadata.st_mode) else None
            relative = "." if path == root else str(path.relative_to(root))
            snapshot[relative] = (
                stat.S_IFMT(metadata.st_mode),
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
                metadata.st_uid,
                metadata.st_gid,
                metadata.st_nlink,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
                raw,
            )
        return snapshot

    def materialize_staged_candidate_as_live(self, staged: object) -> None:
        """Test setup for a recursively retained active receipt chain."""

        stage = json.loads(Path(staged.stage_path).read_bytes())
        for artifact in stage["artifacts"]:
            if artifact["role"] in {
                "prior-active-record",
                "prior-deployment-alias",
            }:
                continue
            target = Path(artifact["installed"]["path"])
            raw = Path(artifact["staged"]["path"]).read_bytes()
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            for parent in target.parents:
                if parent == staged.plan.precondition.canonical_root.parent:
                    break
                if parent.is_relative_to(staged.plan.precondition.canonical_root):
                    parent.chmod(0o700)
            target.write_bytes(raw)
            target.chmod(artifact["installed"]["mode"])

    def next_candidate(
        self,
        source: Path,
        name: str,
        version: str,
    ) -> Path:
        destination = self.root / name
        shutil.copytree(source, destination)
        runtime = destination / "runtime" / "task_witness.py"
        runtime.write_bytes(runtime.read_bytes() + f"\n# {name}\n".encode())
        set_agent_plugins_candidate_version(destination, version)
        return destination
