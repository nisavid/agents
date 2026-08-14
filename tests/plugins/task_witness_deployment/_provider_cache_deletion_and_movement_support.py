from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from tests.plugins.task_witness_client._support import (
    CLIENT_ENVIRONMENT,
    INVOCATION_PROFILE_DRIVER_SOURCE,
    LAUNCHER_MODULE_DRIVER_SOURCE,
)

from ._routine_support import RoutineDeploymentFixture
from ._support import (
    ProviderFixture,
    canonical_bytes,
    canonical_document,
    content_document,
    set_agent_plugins_candidate_version,
    sha256,
    validator_identity,
    write_agent_plugins_manifest,
)


@dataclass(frozen=True)
class ExternalProviderCandidates:
    manager_cache: Path
    candidate_a: Path
    candidate_b: Path
    identity: dict[str, object]


@dataclass(frozen=True)
class RetainedModuleOracle:
    name: str
    relative_path: str
    installed_path: Path
    raw: bytes
    length: int
    sha256: str


@dataclass(frozen=True)
class ProviderCandidateOracle:
    declaration_raw: bytes
    declaration_sha256: str
    declaration_value: dict[str, object]
    declaration_content_sha256: str
    modules: tuple[RetainedModuleOracle, ...]
    validator_implementation_sha256: str
    trust_context_path: Path
    trust_context_raw: bytes
    trust_context_sha256: str
    trust_context_binding: dict[str, str]
    provider_receipt_projection: dict[str, object]
    projection: dict[str, object]


@dataclass(frozen=True)
class OpenReadAttempt:
    role: str
    source: str
    raw: str
    lexical: Path
    resolved: Path


@dataclass(frozen=True)
class AuditedClientInvocation:
    completed: subprocess.CompletedProcess[bytes]
    open_read_attempts: tuple[OpenReadAttempt, ...]
    failed_probe_attempts: tuple[OpenReadAttempt, ...]
    dir_fd_forbidden_probe_attempts: tuple[OpenReadAttempt, ...]


@dataclass(frozen=True)
class PreparedProviderFirstInstall:
    canonical_root: Path
    staged: object
    activation: object


def _open_read_audit_prelude(
    audit_path: Path,
    role: str,
    failed_probe: Path,
    forbidden_probe_roots: tuple[Path, ...],
) -> bytes:
    return f"""
import fcntl as _open_audit_fcntl
import json as _open_audit_json
import os as _open_audit_os
import stat as _open_audit_stat
import sys as _open_audit_sys
import threading as _open_audit_threading
_open_audit_path = {str(audit_path)!r}
_open_audit_role = {role!r}
_open_audit_probe = {str(failed_probe)!r}
_open_audit_forbidden_probe_roots = {
        tuple(str(root) for root in forbidden_probe_roots)!r
    }
_open_audit_original_open = _open_audit_os.open
_open_audit_original_write = _open_audit_os.write
_open_audit_fd = _open_audit_original_open(
    _open_audit_path,
    _open_audit_os.O_WRONLY
    | _open_audit_os.O_APPEND
    | getattr(_open_audit_os, "O_CLOEXEC", 0),
)
_open_audit_os.set_inheritable(_open_audit_fd, False)
_open_audit_state = _open_audit_threading.local()

def _directory_fd_path(descriptor):
    if not isinstance(descriptor, int):
        raise RuntimeError("open/read audit directory descriptor is invalid")
    if descriptor == getattr(_open_audit_os, "AT_FDCWD", -100):
        return _open_audit_os.getcwd()
    metadata = _open_audit_os.fstat(descriptor)
    if not _open_audit_stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError("open/read audit directory descriptor is not a directory")
    if _open_audit_sys.platform == "darwin":
        if not hasattr(_open_audit_fcntl, "F_GETPATH"):
            raise RuntimeError("open/read audit F_GETPATH is unavailable")
        raw_path = _open_audit_fcntl.fcntl(
            descriptor,
            _open_audit_fcntl.F_GETPATH,
            bytes(1024),
        ).split(b"\\0", 1)[0]
        directory = _open_audit_os.fsdecode(raw_path)
    elif _open_audit_sys.platform.startswith("linux"):
        directory = _open_audit_os.readlink(f"/proc/self/fd/{{descriptor}}")
        if directory.endswith(" (deleted)"):
            raise RuntimeError("open/read audit directory descriptor was deleted")
    else:
        raise RuntimeError("open/read audit platform is unsupported")
    if not directory or not _open_audit_os.path.isabs(directory):
        raise RuntimeError("open/read audit directory descriptor path is invalid")
    directory = _open_audit_os.path.realpath(directory)
    visible = _open_audit_os.stat(directory, follow_symlinks=True)
    if (
        (visible.st_dev, visible.st_ino) != (metadata.st_dev, metadata.st_ino)
        or not _open_audit_stat.S_ISDIR(visible.st_mode)
    ):
        raise RuntimeError("open/read audit directory descriptor path drifted")
    return directory

def _record_path_open(target, source, *, dir_fd=None):
    if not isinstance(target, (str, bytes, _open_audit_os.PathLike)):
        return
    raw = _open_audit_os.fsdecode(target)
    if not raw:
        return
    if _open_audit_os.path.isabs(raw) or dir_fd is None:
        lexical = _open_audit_os.path.abspath(raw)
    else:
        lexical = _open_audit_os.path.abspath(
            _open_audit_os.path.join(_directory_fd_path(dir_fd), raw)
        )
    resolved = _open_audit_os.path.realpath(lexical)
    record = _open_audit_json.dumps(
        {{
            "lexical": lexical,
            "raw": raw,
            "resolved": resolved,
            "role": _open_audit_role,
            "source": source,
        }},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii") + b"\\n"
    _open_audit_original_write(_open_audit_fd, record)

def _record_open_attempt(event, arguments):
    if (
        event != "open"
        or not arguments
        or getattr(_open_audit_state, "inside_os_open", False)
    ):
        return
    _record_path_open(arguments[0], "audit")

_open_audit_sys.addaudithook(_record_open_attempt)

def _audited_os_open(target, flags, mode=0o777, *, dir_fd=None):
    _record_path_open(target, "os.open", dir_fd=dir_fd)
    previous = getattr(_open_audit_state, "inside_os_open", False)
    _open_audit_state.inside_os_open = True
    try:
        if dir_fd is None:
            return _open_audit_original_open(target, flags, mode)
        return _open_audit_original_open(target, flags, mode, dir_fd=dir_fd)
    finally:
        _open_audit_state.inside_os_open = previous

_open_audit_os.open = _audited_os_open
try:
    _unexpected_probe_fd = _open_audit_os.open(
        _open_audit_probe,
        _open_audit_os.O_RDONLY | getattr(_open_audit_os, "O_CLOEXEC", 0),
    )
except FileNotFoundError:
    pass
else:
    _open_audit_os.close(_unexpected_probe_fd)
    raise RuntimeError("open/read audit missing-path probe unexpectedly succeeded")

for _open_audit_forbidden_probe_root in _open_audit_forbidden_probe_roots:
    _open_audit_forbidden_probe = _open_audit_os.path.join(
        _open_audit_forbidden_probe_root,
        "dir-fd-failed-open-probe",
    )
    if _open_audit_os.path.lexists(_open_audit_forbidden_probe):
        raise RuntimeError("open/read audit forbidden probe unexpectedly exists")
    _open_audit_forbidden_ancestor = _open_audit_os.path.dirname(
        _open_audit_forbidden_probe_root
    )
    _open_audit_forbidden_relative = _open_audit_os.path.relpath(
        _open_audit_forbidden_probe_root,
        _open_audit_forbidden_ancestor,
    )
    if (
        _open_audit_forbidden_relative in {{"", ".", ".."}}
        or _open_audit_forbidden_relative.startswith(".." + _open_audit_os.sep)
        or _open_audit_os.path.isabs(_open_audit_forbidden_relative)
    ):
        raise RuntimeError("open/read audit forbidden probe path is invalid")
    _open_audit_forbidden_ancestor_fd = _open_audit_os.open(
        _open_audit_forbidden_ancestor,
        _open_audit_os.O_RDONLY
        | getattr(_open_audit_os, "O_DIRECTORY", 0)
        | getattr(_open_audit_os, "O_CLOEXEC", 0),
    )
    try:
        _open_audit_forbidden_target = _open_audit_os.path.join(
            _open_audit_forbidden_relative,
            "dir-fd-failed-open-probe",
        )
        try:
            _unexpected_forbidden_probe_fd = _open_audit_os.open(
                _open_audit_forbidden_target,
                _open_audit_os.O_RDONLY
                | getattr(_open_audit_os, "O_CLOEXEC", 0),
                dir_fd=_open_audit_forbidden_ancestor_fd,
            )
        except FileNotFoundError:
            pass
        else:
            _open_audit_os.close(_unexpected_forbidden_probe_fd)
            raise RuntimeError(
                "open/read audit forbidden probe unexpectedly succeeded"
            )
    finally:
        _open_audit_os.close(_open_audit_forbidden_ancestor_fd)
""".encode()


def _write_open_audited_client_driver(
    path: Path,
    audit_path: Path,
    failed_probe: Path,
    forbidden_probe_roots: tuple[Path, ...],
    configuration: dict[str, object],
) -> None:
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
        + _open_read_audit_prelude(
            audit_path,
            "client",
            failed_probe,
            forbidden_probe_roots,
        )
        + INVOCATION_PROFILE_DRIVER_SOURCE.read_bytes()
    )


def _write_open_audited_launcher_driver(
    path: Path,
    audit_path: Path,
    failed_probe: Path,
    forbidden_probe_roots: tuple[Path, ...],
) -> None:
    source = LAUNCHER_MODULE_DRIVER_SOURCE.read_bytes()
    future = b"from __future__ import annotations\n"
    if not source.startswith(future):
        raise AssertionError("launcher driver future-import boundary drifted")
    path.write_bytes(
        future
        + _open_read_audit_prelude(
            audit_path,
            "launcher-runtime",
            failed_probe,
            forbidden_probe_roots,
        )
        + source[len(future) :]
    )


def _load_open_read_attempts(path: Path) -> tuple[OpenReadAttempt, ...]:
    attempts = []
    for line in path.read_bytes().splitlines():
        value = json.loads(line)
        if (
            set(value) != {"lexical", "raw", "resolved", "role", "source"}
            or value["role"] not in {"client", "launcher-runtime"}
            or value["source"]
            not in {
                "audit",
                "os.open",
            }
        ):
            raise AssertionError("open/read audit record contract drifted")
        if any(
            not isinstance(value[field], str)
            for field in ("lexical", "raw", "resolved", "role", "source")
        ):
            raise AssertionError("open/read audit record field is invalid")
        lexical = Path(value["lexical"])
        resolved = Path(value["resolved"])
        if not lexical.is_absolute() or not resolved.is_absolute():
            raise AssertionError("open/read audit path is not absolute")
        attempts.append(
            OpenReadAttempt(
                role=value["role"],
                source=value["source"],
                raw=value["raw"],
                lexical=lexical,
                resolved=resolved,
            )
        )
    return tuple(attempts)


def _external_validator_source(provider_revision: str) -> bytes:
    return (
        b"BUNDLE_CONTRACT = 'demo-bundle-v1'\n"
        b"def _validate_bundle(bundle, *, trust_snapshot):\n"
        + (
            "    return {'accepted': True, "
            f"'provider_revision': {provider_revision!r}}}\n"
        ).encode("ascii")
    )


def _normalized_role(value: dict[str, object]) -> dict[str, object]:
    lifecycle = value["lifecycle"]
    if not isinstance(lifecycle, dict):
        raise AssertionError("provider oracle lifecycle is not an object")
    return {
        **{key: item for key, item in value.items() if key != "lifecycle"},
        **lifecycle,
    }


def _intrinsic_smoke_oracle(
    candidate: Path,
    canonical_root: Path,
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    raw = candidate.joinpath("smoke/task_witness_smoke_validator.py").read_bytes()
    raw_sha256 = sha256(raw)
    implementation = validator_identity(
        "task-witness-smoke-bundle-v1",
        "task-witness-smoke-validator",
        [("task-witness-smoke-validator", raw_sha256)],
    )
    lifecycle = {"state": "active", "usable_for_new_publication": True}
    producer = {
        "producer_id": "task-witness-smoke-producer",
        "contract": "task-witness-smoke-bundle-v1",
        "implementation_sha256": sha256(
            canonical_bytes(
                {
                    "contract": ("task-witness-smoke-producer-implementation-v1"),
                    "validator_implementation_sha256": implementation,
                }
            )
        ),
        "validator_id": "task-witness-smoke-validator",
        "validator_contract": "task-witness-smoke-bundle-v1",
        "validator_implementation_sha256": implementation,
        **lifecycle,
    }
    issuer = {
        "issuer_id": "task-witness-smoke-issuer",
        "contract": "task-witness-smoke-issuer-v1",
        "implementation_sha256": sha256(
            canonical_bytes({"contract": "task-witness-smoke-issuer-implementation-v1"})
        ),
        "capabilities": ["activation-smoke"],
        **lifecycle,
    }
    validator = {
        "validator_id": "task-witness-smoke-validator",
        "contract": "task-witness-smoke-bundle-v1",
        "implementation_sha256": implementation,
        "entrypoint": "task-witness-smoke-validator",
        "modules": [
            {
                "name": "task-witness-smoke-validator",
                "path": str(
                    canonical_root
                    / "trust"
                    / "validators"
                    / f"sha256-{implementation}"
                    / "task-witness-smoke-validator.py"
                ),
                "sha256": raw_sha256,
            }
        ],
        **lifecycle,
    }
    return producer, issuer, validator


def external_provider_candidate_oracle(
    candidate: Path,
    canonical_root: Path,
    *,
    provider_revision: str,
) -> ProviderCandidateOracle:
    """Derive retained provider authority solely from pre-lifecycle source bytes."""

    declaration_path = candidate / "task-witness-provider.json"
    declaration_raw = declaration_path.read_bytes()
    declaration_value = json.loads(declaration_raw)
    if canonical_document(declaration_value) != declaration_raw:
        raise AssertionError("provider oracle declaration is not canonical")
    unsigned = {
        key: item for key, item in declaration_value.items() if key != "content_sha256"
    }
    expected_declaration = content_document(unsigned)
    if declaration_value != expected_declaration:
        raise AssertionError("provider oracle declaration content digest disagrees")
    validators = declaration_value["validators"]
    if not isinstance(validators, list) or len(validators) != 1:
        raise AssertionError("provider oracle requires one validator")
    declared_validator = validators[0]
    if not isinstance(declared_validator, dict):
        raise AssertionError("provider oracle validator is not an object")
    declared_modules = declared_validator["modules"]
    if not isinstance(declared_modules, list) or not declared_modules:
        raise AssertionError("provider oracle requires validator modules")

    implementation = validator_identity(
        str(declared_validator["contract"]),
        str(declared_validator["entrypoint"]),
        [(str(module["name"]), str(module["sha256"])) for module in declared_modules],
    )
    if implementation != declared_validator["implementation_sha256"]:
        raise AssertionError("provider oracle validator aggregate identity disagrees")

    modules = []
    for declared in declared_modules:
        relative_path = str(declared["relative_path"])
        raw = candidate.joinpath(relative_path).read_bytes()
        name = str(declared["name"])
        module = RetainedModuleOracle(
            name=name,
            relative_path=relative_path,
            installed_path=(
                canonical_root
                / "trust"
                / "validators"
                / f"sha256-{implementation}"
                / f"{name}.py"
            ),
            raw=raw,
            length=len(raw),
            sha256=sha256(raw),
        )
        if declared["length"] != module.length or declared["sha256"] != module.sha256:
            raise AssertionError("provider oracle declared module identity disagrees")
        modules.append(module)

    producers = [_normalized_role(item) for item in declaration_value["producers"]]
    issuers = [_normalized_role(item) for item in declaration_value["issuers"]]
    external_validator = {
        **{
            key: item
            for key, item in declared_validator.items()
            if key not in {"lifecycle", "modules"}
        },
        "modules": [
            {
                "name": module.name,
                "path": str(module.installed_path),
                "sha256": module.sha256,
            }
            for module in modules
        ],
        **declared_validator["lifecycle"],
    }
    smoke_producer, smoke_issuer, smoke_validator = _intrinsic_smoke_oracle(
        candidate,
        canonical_root,
    )
    trust_unsigned = {
        "schema_version": 1,
        "contract": "task-witness-trust-context-v2",
        "producers": sorted(
            [*producers, smoke_producer],
            key=lambda item: (
                item["producer_id"],
                item["contract"],
                item["implementation_sha256"],
            ),
        ),
        "issuers": sorted(
            [*issuers, smoke_issuer],
            key=lambda item: (
                item["issuer_id"],
                item["contract"],
                item["implementation_sha256"],
            ),
        ),
        "validators": sorted(
            [external_validator, smoke_validator],
            key=lambda item: (
                item["validator_id"],
                item["contract"],
                item["implementation_sha256"],
            ),
        ),
    }
    trust_value = content_document(trust_unsigned)
    trust_raw = canonical_document(trust_value)
    trust_sha256 = sha256(trust_raw)
    trust_path = canonical_root / "trust" / "contexts" / f"sha256-{trust_sha256}.json"
    provider_receipt_projection = {
        "plugin_id": declaration_value["plugin_id"],
        "publisher": declaration_value["publisher"],
        "repository": declaration_value["repository"],
        "authority_profile": declaration_value["authority_profile"],
        "intrinsic": False,
        "declaration_sha256": sha256(declaration_raw),
        "declaration_content_sha256": declaration_value["content_sha256"],
        "producers": producers,
        "issuers": issuers,
        "validators": [external_validator],
        "retained_modules": [
            {
                "name": module.name,
                "path": str(module.installed_path),
                "length": module.length,
                "sha256": module.sha256,
            }
            for module in sorted(modules, key=lambda item: str(item.installed_path))
        ],
    }
    return ProviderCandidateOracle(
        declaration_raw=declaration_raw,
        declaration_sha256=sha256(declaration_raw),
        declaration_value=declaration_value,
        declaration_content_sha256=str(declaration_value["content_sha256"]),
        modules=tuple(modules),
        validator_implementation_sha256=implementation,
        trust_context_path=trust_path,
        trust_context_raw=trust_raw,
        trust_context_sha256=trust_sha256,
        trust_context_binding={"path": str(trust_path), "sha256": trust_sha256},
        provider_receipt_projection=provider_receipt_projection,
        projection={"accepted": True, "provider_revision": provider_revision},
    )


def _install_external_provider_policy(
    candidate: Path,
    provider: ProviderFixture,
) -> dict[str, object]:
    value = content_document(
        {key: item for key, item in provider.value.items() if key != "content_sha256"}
    )
    candidate.joinpath("task-witness-provider.json").write_bytes(
        canonical_document(value)
    )
    shutil.copytree(provider.module_root, candidate / "validators")
    manifest_author = {
        "name": "Demo Publisher",
        "url": "https://github.com/demo-publisher",
    }
    manifest_path = candidate / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        name=value["plugin_id"],
        author=manifest_author,
        repository=value["repository"],
        homepage=f"{value['repository']}/tree/main/plugin",
        version="1.0.0",
    )
    write_agent_plugins_manifest(candidate, manifest)

    policy_path = candidate / "controller" / "policy.json"
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
                {key: item for key, item in policy.items() if key != "content_sha256"}
            )
        )
    )
    return {
        "plugin_id": value["plugin_id"],
        "publisher_id": value["publisher"],
        "manifest_author": manifest_author,
        "repository_id": "demo/provider",
        "repository_url": value["repository"],
        "source_authority": "github-demo-provider",
    }


def external_provider_candidates(
    fixture: RoutineDeploymentFixture,
) -> ExternalProviderCandidates:
    """Build two real manager-cache candidates around one external provider."""

    repository = Path(__file__).resolve().parents[3]
    source = repository / "plugins" / "task-witness"
    manager_cache = fixture.root / "manager-cache"
    provider = ProviderFixture(manager_cache / "provider-cache" / "demo-provider")
    provider.entrypoint.write_bytes(_external_validator_source("a"))
    provider.value = provider.build_value()
    provider.write()
    candidate_a = manager_cache / "manager-checkout-a"
    shutil.copytree(source, candidate_a)
    identity = _install_external_provider_policy(candidate_a, provider)

    candidate_b = manager_cache / "manager-checkout-b"
    shutil.copytree(candidate_a, candidate_b)
    runtime = candidate_b / "runtime" / "task_witness.py"
    runtime.write_bytes(runtime.read_bytes() + b"\n# provider-cache candidate B\n")
    set_agent_plugins_candidate_version(candidate_b, "1.0.1")
    entrypoint = candidate_b / "validators" / "validator.py"
    entrypoint.write_bytes(_external_validator_source("b"))
    helper = candidate_b / "validators" / "helper.py"
    helper.write_bytes(helper.read_bytes() + b"VALUE_2 = 'compatible'\n")
    declaration_path = candidate_b / "task-witness-provider.json"
    declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
    validator = declaration["validators"][0]
    for module in validator["modules"]:
        raw = candidate_b.joinpath(module["relative_path"]).read_bytes()
        module.update(length=len(raw), sha256=sha256(raw))
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
    return ExternalProviderCandidates(
        manager_cache=manager_cache,
        candidate_a=candidate_a,
        candidate_b=candidate_b,
        identity=identity,
    )


def prepare_provider_first_install(
    fixture: RoutineDeploymentFixture,
    candidates: ExternalProviderCandidates,
) -> PreparedProviderFirstInstall:
    """Prepare and stage provider A solely through the public deployer API."""

    deployment = fixture.deployment()
    account_home = fixture.first_install.root / "account-home"
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

    routine_request = fixture.request_for_candidate(
        canonical_root,
        "0" * 64,
        candidates.candidate_a,
        release_version="1.0.0",
        revision="a" * 40,
        sequence=7,
        **candidates.identity,
    )
    request = deployment.FirstInstallRequest(
        candidate_root=routine_request.candidate_root,
        canonical_root=routine_request.canonical_root,
        source_selection_raw=routine_request.source_selection_raw,
        source_evidence=routine_request.source_evidence,
        runtime_qualification_raw=routine_request.runtime_qualification_raw,
        maintenance_transaction_sha256="9" * 64,
    )
    prepared = deployment.prepare_first_install(request)
    authorization_raw = fixture.first_install.first_install_authorization_raw(prepared)
    staged = deployment.stage_first_install(
        request,
        authorization_raw,
        fixture.first_install.root / "stage",
    )
    return PreparedProviderFirstInstall(
        canonical_root=canonical_root,
        staged=staged,
        activation=deployment.ActivationRequest(
            deployment=request,
            authorization_raw=authorization_raw,
            stage_receipt=staged.stage_path,
        ),
    )


def invoke_installed_client(
    canonical_root: Path,
    bundle: Path,
    support_root: Path,
    *,
    forbidden_probe_roots: tuple[Path, ...],
    historical_trust_context_sha256: str | None = None,
) -> AuditedClientInvocation:
    """Run through the existing passwd-root and launcher virtualization seam."""

    resolved_forbidden_probe_roots = tuple(
        root.resolve(strict=False) for root in forbidden_probe_roots
    )
    if (
        not forbidden_probe_roots
        or forbidden_probe_roots != resolved_forbidden_probe_roots
        or len(set(forbidden_probe_roots)) != len(forbidden_probe_roots)
        or any(
            not root.is_absolute()
            or not root.parent.is_dir()
            or root.joinpath("dir-fd-failed-open-probe").exists()
            for root in forbidden_probe_roots
        )
    ):
        raise AssertionError("open/read audit forbidden probe roots are invalid")
    support_root.mkdir(mode=0o700)
    audit_path = support_root / "open-read-audit.jsonl"
    audit_path.write_bytes(b"")
    audit_path.chmod(0o600)
    client_probe = support_root / "client-failed-open-probe"
    launcher_probe = support_root / "launcher-runtime-failed-open-probe"
    if client_probe.exists() or launcher_probe.exists():
        raise AssertionError("open/read audit failed-path probe already exists")
    launcher_driver = support_root / "launcher_module_driver.py"
    _write_open_audited_launcher_driver(
        launcher_driver,
        audit_path,
        launcher_probe,
        forbidden_probe_roots,
    )
    client_driver = support_root / "configured_client_driver.py"
    _write_open_audited_client_driver(
        client_driver,
        audit_path,
        client_probe,
        forbidden_probe_roots,
        {
            "scenario": "composed-client",
            "launcher_driver": str(launcher_driver),
        },
    )
    arguments = [
        sys.executable,
        "-B",
        "-I",
        "-S",
        "-X",
        "disable-remote-debug",
        str(client_driver),
        str(canonical_root / "client" / "task_witness_client.py"),
        str(canonical_root),
        "validate",
        "--bundle",
        str(bundle),
    ]
    if historical_trust_context_sha256 is not None:
        arguments.extend(
            [
                "--historical",
                "--trust-context-sha256",
                historical_trust_context_sha256,
            ]
        )
    completed = subprocess.run(
        arguments,
        text=False,
        capture_output=True,
        check=False,
        env=CLIENT_ENVIRONMENT,
        timeout=20,
    )
    attempts = _load_open_read_attempts(audit_path)
    failed_probe_targets = {
        ("client", client_probe.resolve(strict=False)),
        ("launcher-runtime", launcher_probe.resolve(strict=False)),
    }
    failed_probe_attempts = tuple(
        attempt
        for attempt in attempts
        if (attempt.role, attempt.resolved) in failed_probe_targets
    )
    if len(failed_probe_attempts) != 2 or {
        (attempt.role, attempt.source, attempt.resolved)
        for attempt in failed_probe_attempts
    } != {(role, "os.open", target) for role, target in failed_probe_targets}:
        raise AssertionError("open/read audit did not record each failed open probe")
    forbidden_probe_targets = {
        (root / "dir-fd-failed-open-probe").resolve(strict=False)
        for root in forbidden_probe_roots
    }
    dir_fd_forbidden_probe_attempts = tuple(
        attempt for attempt in attempts if attempt.resolved in forbidden_probe_targets
    )
    expected_dir_fd_forbidden_probes = {
        (role, "os.open", target, target)
        for role in ("client", "launcher-runtime")
        for target in forbidden_probe_targets
    }
    if (
        len(dir_fd_forbidden_probe_attempts) != len(expected_dir_fd_forbidden_probes)
        or {
            (attempt.role, attempt.source, attempt.lexical, attempt.resolved)
            for attempt in dir_fd_forbidden_probe_attempts
        }
        != expected_dir_fd_forbidden_probes
    ):
        raise AssertionError(
            "open/read audit did not record each directory-relative forbidden probe"
        )
    return AuditedClientInvocation(
        completed=completed,
        open_read_attempts=attempts,
        failed_probe_attempts=failed_probe_attempts,
        dir_fd_forbidden_probe_attempts=dir_fd_forbidden_probe_attempts,
    )


def regular_file_authority(root: Path) -> tuple[tuple[str, int, int, str], ...]:
    """Return a byte-and-mode identity for every installed regular file."""

    authority = []
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        if stat.S_ISREG(metadata.st_mode):
            raw = path.read_bytes()
            authority.append(
                (
                    path.relative_to(root).as_posix(),
                    stat.S_IMODE(metadata.st_mode),
                    len(raw),
                    sha256(raw),
                )
            )
    return tuple(authority)


def external_provider_bundle(
    root: Path,
    provider_declaration: dict[str, object],
) -> Path:
    bundle = root / "external-provider-bundle"
    bundle.mkdir(mode=0o700)
    producer = provider_declaration["producers"][0]
    manifest = {
        "producer": {
            "producer_id": producer["producer_id"],
            "contract": producer["contract"],
            "implementation_sha256": producer["implementation_sha256"],
        }
    }
    path = bundle / "manifest.json"
    path.write_bytes(canonical_document(manifest))
    path.chmod(0o600)
    return bundle
