from __future__ import annotations

import base64
import fcntl
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[3]
PLUGIN = REPOSITORY / "plugins" / "task-witness"
CLIENT_SOURCE = PLUGIN / "client" / "task_witness_client.py"
CLIENT_DRIVER_SOURCE = Path(__file__).with_name("_client_driver.py")
INVOCATION_PROFILE_DRIVER_SOURCE = Path(__file__).with_name(
    "_invocation_profile_driver.py"
)
LAUNCHER_MODULE_DRIVER_SOURCE = Path(__file__).with_name("_launcher_module_driver.py")
LAUNCHER_BEHAVIOR_DRIVER_SOURCE = Path(__file__).with_name(
    "_launcher_behavior_driver.py"
)
PROCESS_SUPERVISION_DRIVER_SOURCE = Path(__file__).with_name(
    "_process_supervision_driver.py"
)
RETAINED_STATE_DRIVER_SOURCE = Path(__file__).with_name("_retained_state_driver.py")
TERMINAL_OUTPUT_DRIVER_SOURCE = Path(__file__).with_name("_terminal_output_driver.py")
SHIM_TEMPLATE = PLUGIN / "client" / "task_witness_shim.sh.in"
LAUNCHER_SOURCE = PLUGIN / "launcher" / "task_witness_launch.py"
CONTROLLER_SOURCE = PLUGIN / "controller" / "task_witness_deploy.py"
POLICY_SOURCE = PLUGIN / "controller" / "policy.json"
SMOKE_BUNDLE_CONTRACT = "task-witness-smoke-bundle-v1"
SMOKE_PROJECTION_CONTRACT = "task-witness-smoke-projection-v1"
SMOKE_CHALLENGE = "task-witness-activation-smoke-v1"
SMOKE_PRODUCER_NAME = "task-witness-smoke-producer"
SMOKE_ISSUER_NAME = "task-witness-smoke-issuer"
SMOKE_VALIDATOR_NAME = "task-witness-smoke-validator"
RUNTIME_PAYLOAD_SPECS = (
    ("entrypoint", "task_witness.py"),
    ("canonical", "canonical.py"),
    ("bundle-io", "bundle_io.py"),
    ("trust", "trust.py"),
)
CLIENT_ENVIRONMENT = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "TZ": "UTC",
}


def load_client_module(module_name: str) -> ModuleType:
    specification = importlib.util.spec_from_file_location(module_name, CLIENT_SOURCE)
    if specification is None or specification.loader is None:
        raise AssertionError(f"could not load client source as {module_name!r}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _encode_launcher_configuration(value: object) -> object:
    if isinstance(value, bytes):
        return {"bytes_base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _encode_launcher_configuration(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_encode_launcher_configuration(item) for item in value]
    return value


def configured_launcher_source(mode: str, **configuration: object) -> str:
    encoded = _encode_launcher_configuration({"mode": mode, **configuration})
    raw = json.dumps(encoded, sort_keys=True, separators=(",", ":"))
    source = LAUNCHER_BEHAVIOR_DRIVER_SOURCE.read_text(encoding="utf-8")
    return source + f"\n_run_configured_launcher(json.loads({raw!r}))\n"


def install_launcher_behavior(
    fixture: ValidInvocationFixture,
    mode: str,
    **configuration: object,
) -> None:
    fixture.replace_launcher_behavior(
        configured_launcher_source(mode, **configuration),
    )


def write_configured_launcher(
    path: Path,
    mode: str,
    **configuration: object,
) -> None:
    path.write_text(
        configured_launcher_source(mode, **configuration),
        encoding="utf-8",
    )
    path.chmod(0o500)


def canonical_value(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_document(value: object) -> bytes:
    return canonical_value(value) + b"\n"


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def document(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "content_sha256": sha256(canonical_value(value))}


def runtime_identity(payloads: list[dict[str, Any]]) -> str:
    return sha256(
        canonical_value(
            {
                "contract": "task-witness-runtime-artifact-manifest-v2",
                "runtime_contract": "task-witness-runtime-v1",
                "entrypoint_role": "entrypoint",
                "payloads": payloads,
            }
        )
    )


def validator_identity(
    contract: str,
    entrypoint: str,
    modules: list[tuple[str, str]],
) -> str:
    return sha256(
        canonical_value(
            {
                "contract": "task-witness-validator-artifact-manifest-v1",
                "validator_contract": contract,
                "entrypoint_module": entrypoint,
                "modules": [
                    {"name": name, "content_sha256": digest} for name, digest in modules
                ],
            }
        )
    )


def bundle_identity(files: dict[str, bytes]) -> str:
    return sha256(
        canonical_value(
            {
                "contract": "task-witness-bundle-inventory-v1",
                "files": [
                    {
                        "name": name,
                        "length": len(raw),
                        "sha256": sha256(raw),
                    }
                    for name, raw in sorted(files.items())
                ],
            }
        )
    )


def interpreter_identity(executable: Path | None = None) -> dict[str, object]:
    executable = (
        Path(sys.executable).resolve() if executable is None else executable.resolve()
    )
    return {
        "executable": str(executable),
        "implementation": "cpython",
        "version": {
            "major": sys.version_info.major,
            "minor": sys.version_info.minor,
            "micro": sys.version_info.micro,
        },
    }


def installed_file(path: Path) -> dict[str, object]:
    metadata = path.stat()
    return {
        "path": str(path),
        "length": metadata.st_size,
        "sha256": sha256(path.read_bytes()),
        "owner": metadata.st_uid,
        "mode": stat.S_IMODE(metadata.st_mode),
    }


def activation_lock_identity(path: Path) -> dict[str, object]:
    metadata = path.stat()
    return {
        "path": str(path),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "owner": metadata.st_uid,
        "mode": stat.S_IMODE(metadata.st_mode),
    }


def full_filesystem_identity(path: Path) -> list[int]:
    metadata = path.stat()
    return [
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    ]


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def parse_diagnostic(raw: bytes) -> tuple[str, dict[str, str]]:
    text = raw.decode("ascii").removesuffix("\n")
    message, *encoded_fields = text.split(" | ")
    fields = {}
    for encoded_field in encoded_fields:
        name, separator, value = encoded_field.partition("=")
        if not separator or not name or name in fields:
            raise AssertionError(f"invalid diagnostic field: {encoded_field!r}")
        fields[name] = value
    return message, fields


def write_configured_driver(
    path: Path,
    source: Path,
    configuration: dict[str, object],
) -> Path:
    encoded = json.dumps(
        configuration,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    path.write_bytes(
        b"import json\n"
        + f"CONFIG = json.loads({encoded!r})\n".encode()
        + source.read_bytes()
    )
    return path


class ValidInvocationFixture:
    """A complete private Task Witness installation for client tests."""

    def __init__(
        self,
        root: Path,
        *,
        interpreter_executable: Path | None = None,
    ) -> None:
        self.root = root
        self.interpreter_executable = (
            Path(sys.executable).resolve()
            if interpreter_executable is None
            else interpreter_executable.resolve()
        )
        self.account_home = self.root / "home"
        self.install = self.account_home / ".local" / "libexec" / "task-witness"
        self.shim = self.install / "task-witness"
        self.client = self.install / "client" / "task_witness_client.py"
        self.launcher = self.install / "launcher" / "task_witness_launch.py"
        self.controller = self.install / "controller" / "task_witness_deploy.py"
        self.policy = self.install / "controller" / "policy.json"
        self.receipts_directory = self.install / "receipts"
        self.context_directory = self.install / "trust" / "contexts"
        self.validator_directory = self.install / "trust" / "validators"
        self.generation_directory = self.install / "generations"
        self.bundle = self.root / "bundle"
        self.smoke_bundle = self.install / "smoke" / "bundle"
        self.historical_trust_contexts: list[dict[str, str]] = []
        self.validation_deadline_seconds: float = 60
        self.accepted_output_deadline_seconds: float = 60
        self.termination_grace_seconds: float = 2
        self.kill_reap_seconds: float = 1
        self._create_installation()

    def _private_directory(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)

    def _create_installation(self) -> None:
        for directory in (
            self.account_home,
            self.install,
            self.client.parent,
            self.launcher.parent,
            self.controller.parent,
            self.receipts_directory,
            self.context_directory.parent,
            self.context_directory,
            self.validator_directory,
            self.generation_directory,
            self.bundle,
            self.smoke_bundle,
        ):
            self._private_directory(directory)

        shutil.copy2(CLIENT_SOURCE, self.client)
        self.client.chmod(0o500)
        shutil.copy2(CONTROLLER_SOURCE, self.controller)
        self.controller.chmod(0o500)
        shutil.copy2(POLICY_SOURCE, self.policy)
        self.policy.chmod(0o600)
        self._write_shim()
        self._write_trust_context()
        self._write_bundle()
        self._write_active_record()
        self._write_launcher()
        self._write_deployment_receipt()
        self._write_driver()

    def _write_bundle(self) -> None:
        self.bundle_files = {
            "manifest.json": canonical_document(
                {
                    "producer": {
                        "producer_id": SMOKE_PRODUCER_NAME,
                        "contract": SMOKE_BUNDLE_CONTRACT,
                        "implementation_sha256": self.producer_implementation_sha256,
                    }
                }
            )
        }
        for name, raw in self.bundle_files.items():
            path = self.bundle / name
            path.write_bytes(raw)
            path.chmod(0o600)
            if name == "manifest.json":
                self.bundle_manifest = path
        self.expected_bundle_sha256 = bundle_identity(self.bundle_files)
        self.smoke_bundle_files = {
            "manifest.json": canonical_document(
                {
                    "schema_version": 1,
                    "contract": SMOKE_BUNDLE_CONTRACT,
                    "producer": {
                        "producer_id": SMOKE_PRODUCER_NAME,
                        "contract": SMOKE_BUNDLE_CONTRACT,
                        "implementation_sha256": (self.producer_implementation_sha256),
                    },
                    "challenge": SMOKE_CHALLENGE,
                }
            )
        }
        self.smoke_bundle_manifest = self.smoke_bundle / "manifest.json"
        self.smoke_bundle_manifest.write_bytes(self.smoke_bundle_files["manifest.json"])
        self.smoke_bundle_manifest.chmod(0o600)
        self.smoke_bundle_sha256 = bundle_identity(self.smoke_bundle_files)

    def _write_shim(self) -> None:
        template = SHIM_TEMPLATE.read_text(encoding="utf-8")
        rendered = template.replace(
            "@TASK_WITNESS_PYTHON@",
            shell_quote(str(self.interpreter_executable)),
        ).replace(
            "@TASK_WITNESS_CLIENT@",
            shell_quote(str(self.client)),
        )
        self.shim.write_text(rendered, encoding="utf-8")
        self.shim.chmod(0o500)

    def _write_trust_context(self) -> None:
        self.validator_contract = SMOKE_BUNDLE_CONTRACT
        self.validator_module_name = SMOKE_VALIDATOR_NAME
        self.validator_module_raw = (
            b"BUNDLE_CONTRACT = 'task-witness-smoke-bundle-v1'\n"
            b"def _validate_bundle(bundle, *, trust_snapshot):\n"
            b"    manifest, _ = bundle.read_json('manifest.json', 'fixture manifest')\n"
            b"    if manifest.get('challenge') == 'task-witness-activation-smoke-v1':\n"
            b"        return {'schema_version': 1, "
            b"'contract': 'task-witness-smoke-projection-v1', "
            b"'challenge': 'task-witness-activation-smoke-v1', 'accepted': True}\n"
            b"    return {'contract': 'fixture-projection-v1'}\n"
        )
        self.validator_module_sha256 = sha256(self.validator_module_raw)
        self.validator_implementation_sha256 = validator_identity(
            self.validator_contract,
            self.validator_module_name,
            [(self.validator_module_name, self.validator_module_sha256)],
        )
        self.producer_implementation_sha256 = sha256(
            canonical_value(
                {
                    "contract": "task-witness-smoke-producer-implementation-v1",
                    "validator_implementation_sha256": (
                        self.validator_implementation_sha256
                    ),
                }
            )
        )
        self.issuer_implementation_sha256 = sha256(
            canonical_value({"contract": "task-witness-smoke-issuer-implementation-v1"})
        )
        validator_generation = (
            self.validator_directory / f"sha256-{self.validator_implementation_sha256}"
        )
        self._private_directory(validator_generation)
        self.validator_module = (
            validator_generation / f"{self.validator_module_name}.py"
        )
        self.validator_module.write_bytes(self.validator_module_raw)
        self.validator_module.chmod(0o600)

        self.trust_raw = self._trust_context_raw("active", True)
        self.trust_sha256 = sha256(self.trust_raw)
        self.trust_context = self.context_directory / f"sha256-{self.trust_sha256}.json"
        self.trust_context.write_bytes(self.trust_raw)
        self.trust_context.chmod(0o600)

    def _trust_context_raw(self, state: str, usable_for_new_publication: bool) -> bytes:
        lifecycle = {
            "state": state,
            "usable_for_new_publication": usable_for_new_publication,
        }
        return canonical_document(
            document(
                {
                    "schema_version": 1,
                    "contract": "task-witness-trust-context-v2",
                    "producers": [
                        {
                            "producer_id": SMOKE_PRODUCER_NAME,
                            "contract": SMOKE_BUNDLE_CONTRACT,
                            "implementation_sha256": (
                                self.producer_implementation_sha256
                            ),
                            "validator_id": SMOKE_VALIDATOR_NAME,
                            "validator_contract": self.validator_contract,
                            "validator_implementation_sha256": (
                                self.validator_implementation_sha256
                            ),
                            **lifecycle,
                        }
                    ],
                    "issuers": [
                        {
                            "issuer_id": SMOKE_ISSUER_NAME,
                            "contract": "task-witness-smoke-issuer-v1",
                            "implementation_sha256": (
                                self.issuer_implementation_sha256
                            ),
                            "capabilities": ["activation-smoke"],
                            **lifecycle,
                        }
                    ],
                    "validators": [
                        {
                            "validator_id": SMOKE_VALIDATOR_NAME,
                            "contract": self.validator_contract,
                            "implementation_sha256": (
                                self.validator_implementation_sha256
                            ),
                            "entrypoint": self.validator_module_name,
                            "modules": [
                                {
                                    "name": self.validator_module_name,
                                    "path": str(self.validator_module),
                                    "sha256": self.validator_module_sha256,
                                }
                            ],
                            **lifecycle,
                        }
                    ],
                }
            )
        )

    def add_historical_context(
        self,
        *,
        state: str = "historical-usable",
    ) -> tuple[Path, str, bytes]:
        raw = self._trust_context_raw(state, False)
        return self.add_historical_context_raw(raw, state=state)

    def add_historical_context_raw(
        self,
        raw: bytes,
        *,
        state: str = "historical-usable",
    ) -> tuple[Path, str, bytes]:
        digest = sha256(raw)
        path = self.context_directory / f"sha256-{digest}.json"
        path.write_bytes(raw)
        path.chmod(0o600)
        self.historical_trust_contexts.append(
            {
                "path": str(path),
                "sha256": digest,
                "state": state,
            }
        )
        self.historical_trust_contexts.sort(key=lambda item: item["sha256"])
        self._write_deployment_receipt()
        return path, digest, raw

    def _write_active_record(self) -> None:
        self.runtime_payload_raw = self._runtime_payload_source()
        self.payloads = [
            {
                "role": role,
                "relative_path": name,
                "length": len(self.runtime_payload_raw[role]),
                "sha256": sha256(self.runtime_payload_raw[role]),
            }
            for role, name in RUNTIME_PAYLOAD_SPECS
        ]
        self.runtime_sha256 = runtime_identity(self.payloads)
        self.generation = f"sha256-{self.runtime_sha256}"
        self.runtime_generation = self.generation_directory / self.generation
        self._private_directory(self.runtime_generation)
        for payload in self.payloads:
            path = self.runtime_generation / payload["relative_path"]
            path.write_bytes(self.runtime_payload_raw[payload["role"]])
            path.chmod(0o600)
            if payload["role"] == "trust":
                self.runtime_trust_payload = path
        self.active = document(
            {
                "schema_version": 1,
                "contract": "task-witness-launch-active-v1",
                "generation": self.generation,
                "runtime_contract": "task-witness-runtime-v1",
                "interpreter": interpreter_identity(self.interpreter_executable),
                "public_release": {
                    "repository": "nisavid/agents",
                    "revision": "0" * 40,
                },
                "payloads": self.payloads,
            }
        )
        self.active_raw = canonical_document(self.active)
        self.active_path = self.install / "active.json"
        self.active_path.write_bytes(self.active_raw)
        self.active_path.chmod(0o600)

        self.expected_anchor = {
            "contract": "task-witness-complete-anchor-v1",
            "generation": self.generation,
            "active_record_sha256": sha256(self.active_raw),
            "runtime_contract": "task-witness-runtime-v1",
            "interpreter": interpreter_identity(self.interpreter_executable),
            "public_release": {
                "repository": "nisavid/agents",
                "revision": "0" * 40,
            },
            "runtime_implementation_sha256": self.runtime_sha256,
            "trust_context_sha256": self.trust_sha256,
            "bundle_sha256": self.expected_bundle_sha256,
            "historical": False,
        }

    def _runtime_payload_source(self) -> dict[str, bytes]:
        variant = getattr(self, "runtime_payload_variant", b"")
        return {
            role: f"{role}\n".encode("ascii") + variant
            for role, _ in RUNTIME_PAYLOAD_SPECS
        }

    def _write_launcher(self) -> None:
        self.witness = {
            "contract": "task-witness-canonical-projection-v2",
            "bundle_sha256": self.expected_bundle_sha256,
            "producer": {
                "producer_id": SMOKE_PRODUCER_NAME,
                "contract": SMOKE_BUNDLE_CONTRACT,
                "implementation_sha256": self.producer_implementation_sha256,
                "validator_id": SMOKE_VALIDATOR_NAME,
                "validator_contract": SMOKE_BUNDLE_CONTRACT,
                "validator_implementation_sha256": (
                    self.validator_implementation_sha256
                ),
            },
            "validator": {
                "validator_id": SMOKE_VALIDATOR_NAME,
                "contract": SMOKE_BUNDLE_CONTRACT,
                "implementation_sha256": self.validator_implementation_sha256,
            },
            "projection": {"contract": "fixture-projection-v1"},
            "trust_context_sha256": self.trust_sha256,
            "historical": False,
        }
        self.write_launcher_envelope(
            {
                "contract": "task-witness-launch-envelope-v1",
                "anchor": self.expected_anchor,
                "witness": self.witness,
            }
        )

    def write_launcher_envelope(self, envelope: dict[str, Any]) -> None:
        self.envelope = envelope
        self.envelope_raw = canonical_document(envelope)
        self.write_launcher_behavior(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            f"sys.stdout.buffer.write({self.envelope_raw!r})\n"
        )

    def replace_launcher_envelope(self, envelope: dict[str, Any]) -> None:
        self.write_launcher_envelope(envelope)
        self._write_deployment_receipt()

    def replace_launcher_behavior(self, source: str) -> None:
        self.write_launcher_behavior(source)
        self._write_deployment_receipt()

    def write_launcher_behavior(self, source: str) -> None:
        if self.launcher.exists():
            self.launcher.chmod(0o700)
        self.launcher.write_text(source, encoding="utf-8")
        self.launcher.chmod(0o500)

    def _write_deployment_receipt(self) -> None:
        previous_rollback_receipt = getattr(self, "rollback_receipt", None)
        previous_deployment_receipt = getattr(
            self,
            "retained_deployment_receipt",
            None,
        )
        self.lock = self.install / "activation.lock"
        if not self.lock.exists():
            self.lock.touch(mode=0o600)
        activation_lock = activation_lock_identity(self.lock)
        self.process_profile = {
            "contract": "task-witness-process-profile-v2",
            "interpreter_flags": [
                "-B",
                "-I",
                "-S",
                "-X",
                "disable-remote-debug",
            ],
            "environment": {
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "TZ": "UTC",
            },
            "cwd": "/",
            "stdin": "closed",
            "close_fds": True,
            "new_session": True,
            "restore_signals": True,
            "umask": 0o077,
            "validation_deadline_seconds": self.validation_deadline_seconds,
            "accepted_output_deadline_seconds": (self.accepted_output_deadline_seconds),
            "termination_grace_seconds": self.termination_grace_seconds,
            "kill_reap_seconds": self.kill_reap_seconds,
            "post_leader_pipe_drain_seconds": 1,
            "stdout_max_bytes": 4 * 1024 * 1024,
            "stderr_max_bytes": 256 * 1024,
            "diagnostic_max_bytes": 4 * 1024,
            "diagnostic_write_seconds": 0.05,
            "io_chunk_bytes": 64 * 1024,
            "shared_lock_seconds": 2,
            "exclusive_lock_seconds": 65,
        }
        policy = json.loads(self.policy.read_bytes())
        policy.pop("content_sha256")
        policy["control_surface"]["process_profile"] = self.process_profile
        policy = document(policy)
        self.policy.write_bytes(canonical_document(policy))
        self.policy.chmod(0o600)
        self.interpreter = {
            **interpreter_identity(self.interpreter_executable),
            "executable_sha256": sha256(self.interpreter_executable.read_bytes()),
        }
        self.rollback_receipt_value = document(
            {
                "schema_version": 1,
                "contract": "task-witness-rollback-receipt-v1",
                "state": "absent",
                "canonical_root": str(self.install),
                "effective_uid": os.geteuid(),
                "activation_lock": activation_lock,
                "precondition": {
                    "root_identity": full_filesystem_identity(self.install),
                    "activation_lock_identity": full_filesystem_identity(self.lock),
                },
                "deployment_receipt_absent": True,
                "prior_activation_unit": [],
                "external_dependencies": [],
                "smoke": {
                    "contract": "task-witness-first-install-rollback-v1",
                    "expected_state": "absent",
                },
            }
        )
        self.rollback_receipt_raw = canonical_document(self.rollback_receipt_value)
        rollback_sha256 = sha256(self.rollback_receipt_raw)
        self.rollback_receipt = (
            self.receipts_directory / f"sha256-{rollback_sha256}.json"
        )
        if (
            previous_rollback_receipt is not None
            and previous_rollback_receipt != self.rollback_receipt
            and previous_rollback_receipt.is_file()
        ):
            previous_rollback_receipt.unlink()
        self.rollback_receipt.write_bytes(self.rollback_receipt_raw)
        self.rollback_receipt.chmod(0o600)

        release_revision = "0" * 40
        source_subtree_sha256 = sha256(
            canonical_value(
                {
                    "client": sha256(CLIENT_SOURCE.read_bytes()),
                    "controller": sha256(CONTROLLER_SOURCE.read_bytes()),
                    "launcher": sha256(LAUNCHER_SOURCE.read_bytes()),
                    "policy": sha256(POLICY_SOURCE.read_bytes()),
                }
            )
        )
        lineage = {"lineage_id": "agents-stable", "sequence": 1}
        manager_receipt_raw = b"fixture manager receipt\n"
        manager_receipt_sha256 = sha256(manager_receipt_raw)
        self.source_selection = document(
            {
                "schema_version": 1,
                "contract": "task-witness-source-selection-v1",
                "mode": "harness_snapshot",
                "publisher_id": "nisavid",
                "manifest_author": {
                    "name": "Ivan D Vasin",
                    "url": "https://github.com/nisavid",
                },
                "repository_id": "nisavid/agents",
                "repository_url": "https://github.com/nisavid/agents",
                "release_version": "1.0.0",
                "revision": release_revision,
                "subtree_sha256": source_subtree_sha256,
                "source_authority": "github-nisavid-agents",
                "details": {
                    "harness": "codex",
                    "manager": "fixture-plugin-manager",
                    "channel": "stable",
                    "manager_trust_class": "operator-installed",
                    "manager_receipt_sha256": manager_receipt_sha256,
                    "lineage": lineage,
                },
            }
        )
        source_selection_raw = canonical_document(self.source_selection)
        manager_binding_claims = {
            "plugin_id": "task-witness",
            "release_version": "1.0.0",
            "revision": release_revision,
            "subtree_sha256": source_subtree_sha256,
            "channel": "stable",
            "manager_trust_class": "operator-installed",
            "source_authority": "github-nisavid-agents",
            "lineage": lineage,
        }
        self.manager_binding = document(
            {
                "schema_version": 1,
                "contract": "task-witness-manager-binding-v1",
                "harness": "codex",
                "manager": "fixture-plugin-manager",
                "adapter_sha256": sha256(b"fixture manager adapter\n"),
                "manager_receipt_sha256": manager_receipt_sha256,
                "claims": manager_binding_claims,
            }
        )
        manager_binding_raw = canonical_document(self.manager_binding)
        source_evidence_sha256 = sha256(
            canonical_value(
                {
                    "contract": "task-witness-source-evidence-v1",
                    "mode": "harness_snapshot",
                    "binding_sha256": sha256(manager_binding_raw),
                    "record_sha256": manager_receipt_sha256,
                }
            )
        )
        source = {
            "mode": "harness_snapshot",
            "plugin_id": "task-witness",
            "publisher_id": "nisavid",
            "manifest_author": {
                "name": "Ivan D Vasin",
                "url": "https://github.com/nisavid",
            },
            "repository_id": "nisavid/agents",
            "repository_url": "https://github.com/nisavid/agents",
            "release_version": "1.0.0",
            "revision": release_revision,
            "subtree_sha256": source_subtree_sha256,
            "source_authority": "github-nisavid-agents",
            "details": {
                "channel": "stable",
                "trust_class": "operator-installed",
                "lineage": lineage,
            },
            "source_selection_sha256": sha256(source_selection_raw),
            "source_selection_content_sha256": self.source_selection["content_sha256"],
            "source_evidence": {
                "kind": "harness_snapshot",
                "source_evidence_sha256": source_evidence_sha256,
                "adapter_sha256": self.manager_binding["adapter_sha256"],
                "manager_binding_sha256": sha256(manager_binding_raw),
                "manager_binding_content_sha256": self.manager_binding[
                    "content_sha256"
                ],
                "manager_receipt_sha256": manager_receipt_sha256,
            },
            "agent_plugin_manifest_sha256": sha256(
                b"fixture Agent Plugins v1 manifest\n"
            ),
            "claude_manifest_sha256": sha256(
                (PLUGIN / ".claude-plugin" / "plugin.json").read_bytes()
            ),
            "provider_declaration_sha256": None,
            "provider_declaration_content_sha256": None,
        }

        platform_identity = {
            "system": platform.system().lower(),
            "machine": platform.machine().lower(),
            "qualified_filesystem_class": "local-private-filesystem",
        }
        runtime_closure_evidence = {
            "supplier": "homebrew",
            "provenance": "locally-qualified-package",
            "qualification_class": "two-platform-release-gate",
            "evidence_sha256": sha256(b"fixture runtime-closure evidence\n"),
        }
        dependency_classes = [
            "cpython-stdlib",
            "dynamic-loader",
            "system-libraries",
        ]
        runtime_qualification = document(
            {
                "schema_version": 1,
                "contract": "task-witness-runtime-qualification-v1",
                "platform": platform_identity,
                "main_executable": {
                    "path": self.interpreter["executable"],
                    "length": self.interpreter_executable.stat().st_size,
                    "sha256": self.interpreter["executable_sha256"],
                    "implementation": self.interpreter["implementation"],
                    "version": self.interpreter["version"],
                },
                "runtime_closure": runtime_closure_evidence,
                "dependency_classes": dependency_classes,
            }
        )
        runtime_closure = {
            **runtime_closure_evidence,
            "dependency_classes": dependency_classes,
            "qualification_content_sha256": runtime_qualification["content_sha256"],
        }

        trust_context_value = json.loads(self.trust_raw)
        role_inventory = {
            category: trust_context_value[category]
            for category in ("producers", "issuers", "validators")
        }
        intrinsic_provider_content_sha256 = sha256(
            canonical_value(
                {
                    "contract": "task-witness-intrinsic-smoke-provider-v1",
                    "validator_implementation_sha256": (
                        self.validator_implementation_sha256
                    ),
                }
            )
        )
        intrinsic_provider_declaration_raw = canonical_document(
            {
                "contract": "task-witness-intrinsic-smoke-provider-v1",
                "content_sha256": intrinsic_provider_content_sha256,
            }
        )
        providers = [
            {
                "plugin_id": "task-witness",
                "publisher": "nisavid",
                "repository": "https://github.com/nisavid/agents",
                "authority_profile": "task-witness-smoke",
                "intrinsic": True,
                "declaration_sha256": sha256(intrinsic_provider_declaration_raw),
                "declaration_content_sha256": intrinsic_provider_content_sha256,
                "producers": role_inventory["producers"],
                "issuers": role_inventory["issuers"],
                "validators": role_inventory["validators"],
                "retained_modules": [
                    {
                        "name": self.validator_module_name,
                        "path": str(self.validator_module),
                        "length": len(self.validator_module_raw),
                        "sha256": self.validator_module_sha256,
                    }
                ],
            }
        ]
        control_set = {
            "shim": installed_file(self.shim),
            "client": installed_file(self.client),
            "launcher": installed_file(self.launcher),
            "controller": installed_file(self.controller),
            "policy": installed_file(self.policy),
        }
        policy_value = json.loads(self.policy.read_bytes())
        compatibility_policy = {
            **control_set["policy"],
            "content_sha256": policy_value["content_sha256"],
        }
        plan_sha256 = sha256(
            canonical_value(
                {
                    "contract": "fixture-deployment-plan-v1",
                    "canonical_root": str(self.install),
                    "control_set": control_set,
                    "active_record_sha256": sha256(self.active_raw),
                    "trust_context_sha256": self.trust_sha256,
                }
            )
        )
        maintenance_transaction_sha256 = sha256(b"fixture maintenance transaction\n")
        self.authorization = document(
            {
                "schema_version": 1,
                "contract": "task-witness-deployer-authorization-v1",
                "purpose": "first-install",
                "canonical_root": str(self.install),
                "effective_uid": os.geteuid(),
                "plan_sha256": plan_sha256,
                "maintenance_transaction_sha256": maintenance_transaction_sha256,
                "candidate_controller_sha256": control_set["controller"]["sha256"],
                "candidate_policy_sha256": control_set["policy"]["sha256"],
                "source_selection_sha256": source["source_selection_sha256"],
                "source_evidence_sha256": source_evidence_sha256,
            }
        )
        authorization_raw = canonical_document(self.authorization)
        self.smoke_expected_projection = {
            "schema_version": 1,
            "contract": SMOKE_PROJECTION_CONTRACT,
            "challenge": SMOKE_CHALLENGE,
            "accepted": True,
        }
        self.smoke_producer = {
            "producer_id": SMOKE_PRODUCER_NAME,
            "contract": SMOKE_BUNDLE_CONTRACT,
            "implementation_sha256": self.producer_implementation_sha256,
            "validator_id": SMOKE_VALIDATOR_NAME,
            "validator_contract": SMOKE_BUNDLE_CONTRACT,
            "validator_implementation_sha256": (self.validator_implementation_sha256),
        }
        self.smoke_validator = {
            "validator_id": SMOKE_VALIDATOR_NAME,
            "contract": SMOKE_BUNDLE_CONTRACT,
            "implementation_sha256": self.validator_implementation_sha256,
        }
        self.smoke_expected_anchor = {
            **self.expected_anchor,
            "bundle_sha256": self.smoke_bundle_sha256,
        }
        self.smoke_expected_witness = {
            "contract": "task-witness-canonical-projection-v2",
            "bundle_sha256": self.smoke_bundle_sha256,
            "producer": self.smoke_producer,
            "validator": self.smoke_validator,
            "projection": self.smoke_expected_projection,
            "trust_context_sha256": self.trust_sha256,
            "historical": False,
        }
        self.smoke_expected_envelope = {
            "contract": "task-witness-launch-envelope-v1",
            "anchor": self.smoke_expected_anchor,
            "witness": self.smoke_expected_witness,
        }
        self.smoke_expected_envelope_sha256 = sha256(
            canonical_document(self.smoke_expected_envelope)
        )
        self.smoke_receipt = {
            "bundle": {
                "path": str(self.smoke_bundle),
                "sha256": self.smoke_bundle_sha256,
                "manifest": installed_file(self.smoke_bundle_manifest),
            },
            "trust_context": {
                "path": str(self.trust_context),
                "sha256": self.trust_sha256,
            },
            "producer": self.smoke_producer,
            "validator": self.smoke_validator,
            "expected_projection": self.smoke_expected_projection,
            "expected_anchor": self.smoke_expected_anchor,
            "expected_envelope_sha256": self.smoke_expected_envelope_sha256,
        }
        self.receipt = document(
            {
                "schema_version": 2,
                "contract": "task-witness-deployment-receipt-v2",
                "sequence": 1,
                "prior_receipt_sha256": None,
                "canonical_root": str(self.install),
                "effective_uid": os.geteuid(),
                "activation_lock": activation_lock,
                "control_set": control_set,
                "interpreter": self.interpreter,
                "process_profile": self.process_profile,
                "active": {
                    "record_path": str(self.active_path),
                    "record_sha256": sha256(self.active_raw),
                    "generation": self.generation,
                    "runtime_contract": "task-witness-runtime-v1",
                    "runtime_implementation_sha256": self.runtime_sha256,
                    "public_release": {
                        "repository": source["repository_id"],
                        "revision": source["revision"],
                    },
                },
                "trust_context": {
                    "path": str(self.trust_context),
                    "sha256": self.trust_sha256,
                },
                "historical_trust_contexts": self.historical_trust_contexts,
                "platform": platform_identity,
                "source": source,
                "runtime_closure": runtime_closure,
                "contracts": {
                    "active": "task-witness-launch-active-v1",
                    "runtime": "task-witness-runtime-v1",
                    "runtime_artifact_manifest": (
                        "task-witness-runtime-artifact-manifest-v2"
                    ),
                    "envelope": "task-witness-launch-envelope-v1",
                    "anchor": "task-witness-complete-anchor-v1",
                    "canonical_projection": ("task-witness-canonical-projection-v2"),
                    "trust_context": "task-witness-trust-context-v2",
                    "process_profile": "task-witness-process-profile-v2",
                    "source_selection": "task-witness-source-selection-v1",
                    "manager_binding": "task-witness-manager-binding-v1",
                    "compatibility_policy": ("task-witness-compatibility-policy-v2"),
                    "deployment_receipt": "task-witness-deployment-receipt-v2",
                    "rollback_receipt": "task-witness-rollback-receipt-v1",
                },
                "providers": providers,
                "role_inventory": role_inventory,
                "smoke": self.smoke_receipt,
                "compatibility_policy": compatibility_policy,
                "authorization": {
                    "contract": "task-witness-deployer-authorization-v1",
                    "purpose": "first-install",
                    "sha256": sha256(authorization_raw),
                    "content_sha256": self.authorization["content_sha256"],
                    "plan_sha256": plan_sha256,
                    "maintenance_transaction_sha256": (maintenance_transaction_sha256),
                },
                "rollback": {
                    "state": "absent",
                    "path": str(self.rollback_receipt),
                    "sha256": rollback_sha256,
                },
            }
        )
        self.deployment = self.install / "deployment.json"
        self.deployment_raw = canonical_document(self.receipt)
        self.deployment.write_bytes(self.deployment_raw)
        self.deployment.chmod(0o600)
        deployment_sha256 = sha256(self.deployment_raw)
        self.retained_deployment_receipt = (
            self.receipts_directory / f"sha256-{deployment_sha256}.json"
        )
        if (
            previous_deployment_receipt is not None
            and previous_deployment_receipt != self.retained_deployment_receipt
            and previous_deployment_receipt.is_file()
        ):
            previous_deployment_receipt.unlink()
        self.retained_deployment_receipt.write_bytes(self.deployment_raw)
        self.retained_deployment_receipt.chmod(0o600)

    def _write_driver(self) -> None:
        self.driver = self.root / "client_driver.py"
        self.substitute_interpreter = (
            self.interpreter_executable != Path(sys.executable).resolve()
        )
        if self.substitute_interpreter:
            write_configured_driver(
                self.driver,
                CLIENT_DRIVER_SOURCE,
                {
                    "scenario": "substitute-interpreter",
                    "main_argv_start": 4,
                    "pinned_executable": str(self.interpreter_executable),
                },
            )
        else:
            write_configured_driver(
                self.driver,
                CLIENT_DRIVER_SOURCE,
                {"scenario": "plain"},
            )

    def invoke(
        self,
        *public_args: str,
        env: dict[str, str] | None = None,
        timeout: float | None = 10,
        driver: Path | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            self.command(*public_args, driver=driver),
            text=False,
            capture_output=True,
            check=False,
            env=CLIENT_ENVIRONMENT if env is None else env,
            timeout=timeout,
        )

    def command(
        self,
        *public_args: str,
        driver: Path | None = None,
    ) -> list[str]:
        command = [
            sys.executable,
            "-B",
            "-I",
            "-S",
            "-X",
            "disable-remote-debug",
            str(self.driver if driver is None else driver),
            str(self.client),
            str(self.install),
        ]
        if self.substitute_interpreter:
            command.append(str(self.interpreter_executable))
        return [*command, *public_args]

    def invoke_with_open_swap(
        self,
        target: Path,
        replacement: Path,
        audit_marker: Path,
        *public_args: str,
        timeout: float = 10,
    ) -> subprocess.CompletedProcess[bytes]:
        driver = self.root / "lock_swap_driver.py"
        write_configured_driver(
            driver,
            RETAINED_STATE_DRIVER_SOURCE,
            {
                "scenario": "open-swap",
                "main_argv_start": 6,
                "target": str(target),
                "replacement": str(replacement),
                "marker": str(audit_marker),
            },
        )
        return subprocess.run(
            [
                sys.executable,
                "-B",
                "-I",
                "-S",
                "-X",
                "disable-remote-debug",
                str(driver),
                str(self.client),
                str(self.install),
                str(target),
                str(replacement),
                str(audit_marker),
                *public_args,
            ],
            text=False,
            capture_output=True,
            check=False,
            env=CLIENT_ENVIRONMENT,
            timeout=timeout,
        )

    def invoke_with_visible_lock_identity_drift(
        self,
        marker: Path,
        *public_args: str,
        timeout: float = 10,
    ) -> subprocess.CompletedProcess[bytes]:
        driver = self.root / "visible_lock_identity_drift_driver.py"
        write_configured_driver(
            driver,
            RETAINED_STATE_DRIVER_SOURCE,
            {
                "scenario": "visible-lock-identity-drift",
                "main_argv_start": 3,
                "marker": str(marker),
            },
        )
        return self.invoke(
            *public_args,
            timeout=timeout,
            driver=driver,
        )

    def invoke_with_activation_lock_open_audit(
        self,
        marker: Path,
        *public_args: str,
        timeout: float = 10,
    ) -> subprocess.CompletedProcess[bytes]:
        driver = self.root / "activation_lock_open_audit_driver.py"
        write_configured_driver(
            driver,
            RETAINED_STATE_DRIVER_SOURCE,
            {
                "scenario": "activation-lock-open-audit",
                "main_argv_start": 3,
                "marker": str(marker),
            },
        )
        return self.invoke(
            *public_args,
            timeout=timeout,
            driver=driver,
        )


class ComposedInvocationFixture(ValidInvocationFixture):
    """An exact client, launcher, runtime, and validator composition."""

    def _runtime_payload_source(self) -> dict[str, bytes]:
        return {
            role: (PLUGIN / "runtime" / name).read_bytes()
            for role, name in RUNTIME_PAYLOAD_SPECS
        }

    def _write_launcher(self) -> None:
        super()._write_launcher()
        self.launcher.chmod(0o700)
        shutil.copy2(LAUNCHER_SOURCE, self.launcher)
        self.launcher.chmod(0o500)

    def _write_driver(self) -> None:
        self.substitute_interpreter = False
        launcher_driver = self.root / "composed_launcher_driver.py"
        shutil.copy2(LAUNCHER_MODULE_DRIVER_SOURCE, launcher_driver)
        self.driver = self.root / "composed_client_driver.py"
        write_configured_driver(
            self.driver,
            INVOCATION_PROFILE_DRIVER_SOURCE,
            {
                "scenario": "composed-client",
                "launcher_driver": str(launcher_driver),
            },
        )


class _TaskWitnessClientTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def assert_diagnostic(
        self,
        raw: bytes,
        *,
        message: str,
        validator_code_executed: str,
        active_state_changed: str,
        current_receipt: str,
        next_action: str,
    ) -> None:
        observed_message, fields = parse_diagnostic(raw)
        self.assertEqual(observed_message, f"task witness client rejected: {message}")
        self.assertEqual(
            fields,
            {
                "validator_code_executed": validator_code_executed,
                "active_state_changed": active_state_changed,
                "current_receipt": current_receipt,
                "candidate_receipt": "none",
                "rollback": "not-run",
                "next_action": next_action,
            },
        )

    def configured_observed_writer_driver(
        self,
        fixture: ValidInvocationFixture,
        name: str,
        source: Path,
        configuration: dict[str, object],
    ) -> tuple[Path, Path, Path]:
        writer_pid = self.root / f"{name}-writer-pid"
        writer_reaped = self.root / f"{name}-writer-reaped"
        driver = self.root / f"{name}_writer_driver.py"
        write_configured_driver(
            driver,
            source,
            {
                **configuration,
                "observe_writer": True,
                "pid_marker": str(writer_pid),
                "reaped_marker": str(writer_reaped),
                "main_argv_start": 5,
            },
        )
        return driver, writer_pid, writer_reaped

    def full_pipe(self) -> tuple[int, int, int]:
        read_descriptor, write_descriptor = os.pipe()
        original_flags = fcntl.fcntl(write_descriptor, fcntl.F_GETFL)
        fcntl.fcntl(
            write_descriptor,
            fcntl.F_SETFL,
            original_flags | os.O_NONBLOCK,
        )
        filled_bytes = 0
        try:
            while True:
                try:
                    filled_bytes += os.write(write_descriptor, b"x" * 65536)
                except BlockingIOError:
                    break
        finally:
            fcntl.fcntl(write_descriptor, fcntl.F_SETFL, original_flags)
        return read_descriptor, write_descriptor, filled_bytes

    def invoke_with_installation_sentinel(
        self,
        *public_args: str,
        interpreter_arguments: tuple[str, ...] = (
            "-B",
            "-I",
            "-S",
            "-X",
            "disable-remote-debug",
        ),
        environment: dict[str, str] = CLIENT_ENVIRONMENT,
        sentinel_mode: str = "plain",
        sentinel_configuration: dict[str, object] | None = None,
    ) -> tuple[subprocess.CompletedProcess[bytes], Path]:
        installation_access = self.root / "installation-accessed"
        installation_access.unlink(missing_ok=True)
        driver = self.root / "reject_before_installation_driver.py"
        write_configured_driver(
            driver,
            CLIENT_DRIVER_SOURCE,
            {
                "scenario": "installation-sentinel",
                "installation_sentinel": True,
                "installation_access": str(installation_access),
                "sentinel_mode": sentinel_mode,
                **(sentinel_configuration or {}),
            },
        )
        result = subprocess.run(
            [
                sys.executable,
                *interpreter_arguments,
                str(driver),
                str(CLIENT_SOURCE),
                str(installation_access),
                *public_args,
            ],
            text=False,
            capture_output=True,
            check=False,
            env=environment,
        )
        return result, installation_access
