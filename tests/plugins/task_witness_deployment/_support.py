from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import stat
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[3]
PLUGIN = REPOSITORY / "plugins" / "task-witness"
DEPLOYMENT_SOURCE = PLUGIN / "controller" / "task_witness_deploy.py"
SMOKE_VALIDATOR_SOURCE = PLUGIN / "smoke" / "task_witness_smoke_validator.py"

PROVIDER_CONTRACT = "task-witness-provider-declaration-v1"
TRUST_CONTRACT = "task-witness-trust-context-v2"
VALIDATOR_MANIFEST_CONTRACT = "task-witness-validator-artifact-manifest-v1"
AGENT_PLUGINS_V1_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
_DEPLOYMENT_MODULE: ModuleType | None = None


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_document(value: object) -> bytes:
    return canonical_bytes(value) + b"\n"


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def content_document(unsigned: dict[str, Any]) -> dict[str, Any]:
    return {**unsigned, "content_sha256": sha256(canonical_bytes(unsigned))}


def write_agent_plugins_manifest(
    candidate_root: Path,
    manifest: dict[str, Any],
) -> None:
    """Write one test candidate's root manifest and exact Claude projection."""

    candidate_root.joinpath("plugin.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    interface = manifest["extensions"]["com.openai"]["interface"]
    claude = {
        "name": manifest["name"],
        "displayName": interface["displayName"],
        **{
            key: value
            for key, value in manifest.items()
            if key not in {"$schema", "name", "extensions"}
        },
    }
    claude_path = candidate_root / ".claude-plugin" / "plugin.json"
    claude_path.parent.mkdir(exist_ok=True)
    claude_path.write_text(
        json.dumps(claude, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    codex_root = candidate_root / ".codex-plugin"
    if codex_root.exists():
        shutil.rmtree(codex_root)


def adapt_agent_plugins_candidate(
    candidate_root: Path,
    *,
    version: str | None = None,
) -> Path:
    """Convert a copied legacy test candidate to the public manifest shape."""

    agent_plugin_path = candidate_root / "plugin.json"
    if agent_plugin_path.is_file():
        manifest = json.loads(agent_plugin_path.read_text(encoding="utf-8"))
        if version is not None:
            manifest["version"] = version
        write_agent_plugins_manifest(candidate_root, manifest)
        return candidate_root
    claude_path = candidate_root / ".claude-plugin" / "plugin.json"
    codex_path = candidate_root / ".codex-plugin" / "plugin.json"
    claude = json.loads(claude_path.read_text(encoding="utf-8"))
    codex = json.loads(codex_path.read_text(encoding="utf-8"))
    manifest = {
        "$schema": AGENT_PLUGINS_V1_SCHEMA,
        "name": claude["name"],
        **{
            key: value
            for key, value in claude.items()
            if key not in {"name", "displayName"}
        },
        "extensions": {
            "com.openai": {"interface": codex["interface"]},
        },
    }
    if version is not None:
        manifest["version"] = version
    write_agent_plugins_manifest(candidate_root, manifest)
    return candidate_root


def copy_agent_plugins_candidate(
    source: Path,
    destination: Path,
    *,
    version: str | None = None,
) -> Path:
    shutil.copytree(source, destination)
    return adapt_agent_plugins_candidate(destination, version=version)


def set_agent_plugins_candidate_version(candidate_root: Path, version: str) -> None:
    manifest = json.loads(
        candidate_root.joinpath("plugin.json").read_text(encoding="utf-8")
    )
    manifest["version"] = version
    write_agent_plugins_manifest(candidate_root, manifest)


def validator_identity(
    contract: str,
    entrypoint: str,
    modules: list[tuple[str, str]],
) -> str:
    return sha256(
        canonical_bytes(
            {
                "contract": VALIDATOR_MANIFEST_CONTRACT,
                "validator_contract": contract,
                "entrypoint_module": entrypoint,
                "modules": [
                    {"name": name, "content_sha256": digest} for name, digest in modules
                ],
            }
        )
    )


def load_deployment_module() -> ModuleType:
    global _DEPLOYMENT_MODULE
    if _DEPLOYMENT_MODULE is not None:
        return _DEPLOYMENT_MODULE
    if not DEPLOYMENT_SOURCE.is_file():
        raise AssertionError(
            f"missing production deployment module: {DEPLOYMENT_SOURCE}"
        )
    specification = importlib.util.spec_from_file_location(
        "task_witness_deploy_under_test",
        DEPLOYMENT_SOURCE,
    )
    if specification is None or specification.loader is None:
        raise AssertionError("Task Witness deployment module cannot be loaded")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    try:
        specification.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(specification.name, None)
        raise
    for name in (
        "DeploymentError",
        "FirstInstallAuthorizationFacts",
        "FirstInstallRequest",
        "PreparedFirstInstall",
        "ProviderMaterialization",
        "StagedDeployment",
        "TrustContextMaterialization",
        "VerifiedDeploymentStage",
        "materialize_provider",
        "materialize_intrinsic_smoke_provider",
        "materialize_trust_context",
        "compose_trust_context",
        "prepare_first_install",
        "stage_first_install",
        "verify_deployment_stage",
    ):
        if not hasattr(module, name):
            raise AssertionError(f"Task Witness deployment API is missing {name}")
    _DEPLOYMENT_MODULE = module
    return _DEPLOYMENT_MODULE


class ProviderFixture:
    def __init__(self, root: Path, *, prefix: str = "demo") -> None:
        self.root = root
        self.prefix = prefix
        self.provider_path = root / "task-witness-provider.json"
        self.module_root = root / "validators"
        self.module_root.mkdir(parents=True)
        self.entrypoint = self.module_root / "validator.py"
        self.helper = self.module_root / "helper.py"
        self.entrypoint.write_bytes(
            b"BUNDLE_CONTRACT = 'demo-bundle-v1'\n"
            b"def _validate_bundle(bundle, *, trust_snapshot):\n"
            b"    return {'accepted': True}\n"
        )
        self.helper.write_bytes(b"VALUE = 'helper'\n")
        self.value = self.build_value()
        self.write()

    @property
    def validator_modules(self) -> list[dict[str, Any]]:
        return self.value["validators"][0]["modules"]

    @property
    def implementation_sha256(self) -> str:
        return self.value["validators"][0]["implementation_sha256"]

    def build_value(self) -> dict[str, Any]:
        module_specs = [
            {
                "name": "validator",
                "relative_path": "validators/validator.py",
                "length": len(self.entrypoint.read_bytes()),
                "sha256": sha256(self.entrypoint.read_bytes()),
            },
            {
                "name": "helper",
                "relative_path": "validators/helper.py",
                "length": len(self.helper.read_bytes()),
                "sha256": sha256(self.helper.read_bytes()),
            },
        ]
        implementation = validator_identity(
            "demo-bundle-v1",
            "validator",
            [(item["name"], item["sha256"]) for item in module_specs],
        )
        lifecycle = {"state": "active", "usable_for_new_publication": True}
        unsigned = {
            "schema_version": 1,
            "contract": PROVIDER_CONTRACT,
            "plugin_id": f"{self.prefix}-plugin",
            "publisher": f"{self.prefix}-publisher",
            "repository": f"https://example.invalid/{self.prefix}",
            "authority_profile": f"{self.prefix}-authority",
            "producers": [
                {
                    "producer_id": f"{self.prefix}-producer",
                    "contract": "demo-bundle-v1",
                    "implementation_sha256": sha256(f"{self.prefix}-producer".encode()),
                    "validator_id": f"{self.prefix}-validator",
                    "validator_contract": "demo-bundle-v1",
                    "validator_implementation_sha256": implementation,
                    "lifecycle": lifecycle,
                }
            ],
            "issuers": [
                {
                    "issuer_id": f"{self.prefix}-issuer",
                    "contract": "demo-issuer-v1",
                    "implementation_sha256": sha256(f"{self.prefix}-issuer".encode()),
                    "capabilities": ["operator-choice"],
                    "lifecycle": lifecycle,
                }
            ],
            "validators": [
                {
                    "validator_id": f"{self.prefix}-validator",
                    "contract": "demo-bundle-v1",
                    "implementation_sha256": implementation,
                    "entrypoint": "validator",
                    "modules": module_specs,
                    "lifecycle": lifecycle,
                }
            ],
        }
        return content_document(unsigned)

    def refresh_content_digest(self) -> None:
        unsigned = {
            key: value for key, value in self.value.items() if key != "content_sha256"
        }
        self.value["content_sha256"] = sha256(canonical_bytes(unsigned))

    def refresh_validator_identity(self) -> None:
        validator = self.value["validators"][0]
        validator["implementation_sha256"] = validator_identity(
            validator["contract"],
            validator["entrypoint"],
            [(item["name"], item["sha256"]) for item in validator["modules"]],
        )
        self.value["producers"][0]["validator_implementation_sha256"] = validator[
            "implementation_sha256"
        ]
        self.refresh_content_digest()

    def write(self, raw: bytes | None = None) -> None:
        self.provider_path.write_bytes(
            canonical_document(self.value) if raw is None else raw
        )

    def clone_to(self, destination: Path) -> ProviderFixture:
        shutil.copytree(self.root, destination)
        clone = object.__new__(ProviderFixture)
        clone.root = destination
        clone.prefix = self.prefix
        clone.provider_path = destination / "task-witness-provider.json"
        clone.module_root = destination / "validators"
        clone.entrypoint = clone.module_root / "validator.py"
        clone.helper = clone.module_root / "helper.py"
        clone.value = json.loads(clone.provider_path.read_text(encoding="utf-8"))
        return clone


def assert_private_regular(test_case: Any, path: Path) -> None:
    metadata = path.stat()
    test_case.assertTrue(stat.S_ISREG(metadata.st_mode))
    test_case.assertEqual(stat.S_IMODE(metadata.st_mode) & 0o077, 0)
    test_case.assertEqual(metadata.st_nlink, 1)
