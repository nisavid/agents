"""Inactive broker-only harness rendering and reversible activation."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hmac
import json
import os
from pathlib import Path
import re
import shlex
import stat
from types import MappingProxyType
from typing import Any

from .canonical import canonical_bytes, digest
from .model import deep_freeze, deep_thaw


SUPPORTED_HARNESSES = frozenset({"codex", "claude-code", "cursor"})
OWNED_KEYS = frozenset({"schemaVersion", "broker", "adapter", "active"})
RETIRED_DIRECT_KEYS = frozenset(
    {
        "hindsightApiUrl",
        "hindsightApiToken",
        "bankId",
        "bankIdPrefix",
        "dynamicBankId",
        "tenantToken",
        "bearerToken",
        "apiKey",
        "signingKey",
    }
)
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
ACTIVATION_REQUIREMENTS = ("broker_healthy", "profile_healthy", "adapter_self_test")
CLAUDE_EMPTY_MCP_SETTING = ("enableKnowledgeTools", False)
UPSTREAM_HINDSIGHT_SCRIPTS = frozenset(
    {"recall.py", "retain.py", "session_start.py", "session_end.py"}
)


class ActivationCASMismatch(ValueError):
    """The destination changed before an atomic configuration update."""


def _configuration_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    preserved = {
        key: deep_thaw(item)
        for key, item in value.items()
        if key not in OWNED_KEYS
    }
    return {
        "configuration_digest": digest(value),
        "preserved_config_digest": digest(preserved),
        "preserved_config_present": bool(preserved),
    }


@dataclass(frozen=True)
class RenderedHarness:
    harness_id: str
    rendered: Mapping[str, Any] = field(repr=False)
    prestate: Mapping[str, Mapping[str, Any]] = field(repr=False)
    expected_prestate_digest: str
    retired_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rendered", deep_freeze(self.rendered))
        object.__setattr__(self, "prestate", deep_freeze(self.prestate))
        object.__setattr__(self, "retired_keys", tuple(self.retired_keys))

    def to_dict(self) -> dict[str, Any]:
        return {
            "harness_id": self.harness_id,
            **_configuration_projection(self.rendered),
            "activation_state": (
                "active" if self.rendered.get("active") is True else "inactive"
            ),
            "expected_prestate_digest": self.expected_prestate_digest,
            "retired_keys_digest": digest(list(self.retired_keys)),
            "retired_keys_present": bool(self.retired_keys),
        }


@dataclass(frozen=True)
class NativeHarnessArtifact:
    """Complete inactive controller-owned hook artifact for one harness."""

    harness_id: str
    rendered: Mapping[str, Any] = field(repr=False)
    artifact_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "rendered", deep_freeze(self.rendered))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "harness_id": self.harness_id,
            "artifact_digest": self.artifact_digest,
            "hooks_digest": digest(self.rendered["hooks"]),
            "settings_digest": digest(self.rendered["settings"]),
            "tools_digest": digest(self.rendered["tools"]),
        }


@dataclass(frozen=True)
class NativeActivationPlan:
    harness_id: str
    inventory_digest: str
    artifact_digest: str
    policy_digest: str
    native_artifact_digest: str
    staged_generation_digest: str
    upstream_integration_roots_digest: str
    retired_direct_hooks_digest: str
    expected_prestate_digest: str
    target_digest: str
    prestate: Mapping[str, Any] = field(repr=False)
    target: Mapping[str, Any] = field(repr=False)
    plan_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "prestate", deep_freeze(self.prestate))
        object.__setattr__(self, "target", deep_freeze(self.target))

    def body(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "harness_id": self.harness_id,
            "inventory_digest": self.inventory_digest,
            "artifact_digest": self.artifact_digest,
            "policy_digest": self.policy_digest,
            "native_artifact_digest": self.native_artifact_digest,
            "staged_generation_digest": self.staged_generation_digest,
            "upstream_integration_roots_digest": self.upstream_integration_roots_digest,
            "retired_direct_hooks_digest": self.retired_direct_hooks_digest,
            "expected_prestate_digest": self.expected_prestate_digest,
            "target_digest": self.target_digest,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.body(), "plan_digest": self.plan_digest}

@dataclass(frozen=True)
class NativeActivationOutcome:
    status: str
    reason: str
    configuration: Mapping[str, Any] = field(repr=False)
    plan_digest: str
    rollback_attempted: bool = False
    rollback_succeeded: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "configuration", deep_freeze(self.configuration))

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "configuration_digest": digest(self.configuration),
            "plan_digest": self.plan_digest,
            "rollback_attempted": self.rollback_attempted,
            "rollback_succeeded": self.rollback_succeeded,
        }


@dataclass(frozen=True)
class ActivationPlan:
    harness_id: str
    inventory_digest: str
    artifact_digest: str
    policy_digest: str
    expected_prestate_digest: str
    expected_owned_prestate_digest: str
    owned_target: Mapping[str, Any] = field(repr=False)
    retired_keys: tuple[str, ...]
    requirements: tuple[str, ...]
    plan_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "owned_target", deep_freeze(self.owned_target))
        object.__setattr__(self, "retired_keys", tuple(self.retired_keys))
        object.__setattr__(self, "requirements", tuple(self.requirements))

    def body(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "harness_id": self.harness_id,
            "inventory_digest": self.inventory_digest,
            "artifact_digest": self.artifact_digest,
            "policy_digest": self.policy_digest,
            "expected_prestate_digest": self.expected_prestate_digest,
            "expected_owned_prestate_digest": self.expected_owned_prestate_digest,
            "owned_target": deep_thaw(self.owned_target),
            "retired_keys": list(self.retired_keys),
            "requirements": list(self.requirements),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.body(), "plan_digest": self.plan_digest}


@dataclass(frozen=True)
class ActivationOutcome:
    status: str
    reason: str
    configuration: Mapping[str, Any] = field(repr=False)
    activation_state: str
    plan_digest: str
    rollback_attempted: bool = False
    rollback_succeeded: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "configuration", deep_freeze(self.configuration))

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            **_configuration_projection(self.configuration),
            "activation_state": self.activation_state,
            "plan_digest": self.plan_digest,
            "rollback_attempted": self.rollback_attempted,
            "rollback_succeeded": self.rollback_succeeded,
        }


def _socket_path(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or "://" in value
        or not Path(value).is_absolute()
    ):
        raise ValueError("broker locator must be an absolute Unix socket path")
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def render_harness(
    current: Mapping[str, Any], *, harness_id: str, adapter: str, socket_path: str
) -> RenderedHarness:
    """Merge the exact managed keys into one inactive harness configuration."""

    harness_id = _identifier(harness_id, "harness ID")
    if harness_id not in SUPPORTED_HARNESSES:
        raise ValueError(f"unsupported harness: {harness_id}")
    adapter = _identifier(adapter, "adapter identity")
    socket_path = _socket_path(socket_path)
    if not isinstance(current, Mapping):
        raise ValueError("current harness configuration must be an object")

    prestate = {
        key: ({"present": True, "value": deep_thaw(current[key])} if key in current else {"present": False})
        for key in OWNED_KEYS
    }
    rendered = deep_thaw(current)
    retired_keys = tuple(sorted(RETIRED_DIRECT_KEYS.intersection(rendered)))
    for key in retired_keys:
        rendered.pop(key, None)
    rendered.update(
        {
            "schemaVersion": 1,
            "broker": {"transport": "unix", "path": socket_path, "scope": "user"},
            "adapter": adapter,
            "active": False,
        }
    )
    return RenderedHarness(harness_id, rendered, prestate, digest(current), retired_keys)


def render_harnesses(
    current_by_harness: Mapping[str, Mapping[str, Any]],
    bindings: Mapping[str, str],
    *,
    socket_path: str,
) -> Mapping[str, RenderedHarness]:
    """Render every declared Codex, Claude Code, or Cursor binding inactive."""

    if not isinstance(current_by_harness, Mapping) or not isinstance(bindings, Mapping):
        raise ValueError("harness configurations and bindings must be objects")
    unsupported = set(bindings) - SUPPORTED_HARNESSES
    if unsupported:
        raise ValueError(f"unsupported harness: {sorted(unsupported)[0]}")
    return MappingProxyType(
        {
            harness_id: render_harness(
                current_by_harness.get(harness_id, {}),
                harness_id=harness_id,
                adapter=adapter,
                socket_path=socket_path,
            )
            for harness_id, adapter in bindings.items()
        }
    )


def _absolute_artifact_path(value: str | Path, label: str) -> str:
    selected = Path(value)
    if not selected.is_absolute() or "\x00" in str(selected):
        raise ValueError(f"{label} must be an absolute path")
    return str(selected)


def render_native_harness_artifact(
    harness_id: str,
    *,
    executable: str | Path,
    state_dir: str | Path,
    locator_dir: str | Path,
) -> NativeHarnessArtifact:
    """Render complete hooks and wrapped tools into an inactive artifact."""

    if harness_id not in SUPPORTED_HARNESSES:
        raise ValueError(f"unsupported harness: {harness_id}")
    executable = _absolute_artifact_path(executable, "controller executable")
    state_dir = _absolute_artifact_path(state_dir, "controller state directory")
    locator_dir = _absolute_artifact_path(locator_dir, "bridge locator directory")

    def command(event: str) -> str:
        return shlex.join(
            [
                executable,
                "--state-dir",
                state_dir,
                "harness",
                harness_id,
                event,
                "--locator-dir",
                locator_dir,
            ]
        )

    if harness_id in {"codex", "claude-code"}:
        event_commands = {
            "UserPromptSubmit": ("recall", 5, False),
            "Stop": ("checkpoint", 5, False),
            "PreCompact": ("pre-compact", 10, False),
        }
        if harness_id == "claude-code":
            event_commands["SessionEnd"] = ("close", 10, False)
        hooks = {
            "hooks": {
                name: [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": command(event),
                                "timeout": timeout,
                                **({"async": True} if asynchronous else {}),
                            }
                        ]
                    }
                ]
                for name, (event, timeout, asynchronous) in event_commands.items()
            }
        }
    else:
        hooks = {
            "version": 1,
            "hooks": {
                "sessionStart": [
                    {"command": command("session-start"), "timeout": 5}
                ],
                "stop": [
                    {"command": command("checkpoint"), "timeout": 5}
                ],
                "preCompact": [
                    {"command": command("pre-compact"), "timeout": 10}
                ],
                "sessionEnd": [
                    {"command": command("close"), "timeout": 10}
                ],
            },
        }
    settings = {"autoRecall": False, "autoRetain": False}
    if harness_id == "claude-code":
        # Hindsight's Claude plugin keeps its MCP server alive but advertises
        # zero tools when this upstream setting is false.
        settings[CLAUDE_EMPTY_MCP_SETTING[0]] = CLAUDE_EMPTY_MCP_SETTING[1]
    tools = {
        "recall": {"command": command("tool-recall"), "input": "json-stdin"},
        "reflect": {"command": command("reflect"), "input": "json-stdin"},
        "model": {"command": command("model"), "input": "json-stdin"},
        "status": {"command": command("status"), "input": "json-stdin"},
    }
    rendered = {
        "schemaVersion": 1,
        "controllerOwned": True,
        "active": False,
        "hooks": hooks,
        "settings": settings,
        "tools": tools,
    }
    return NativeHarnessArtifact(harness_id, rendered, digest(rendered))


def _write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        payload = canonical_bytes(value)
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_staging_directory(path: Path) -> None:
    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            _remove_staging_directory(child)
            child.rmdir()
        else:
            child.unlink()


def _read_staged_json(path: Path) -> Any:
    try:
        info = path.lstat()
    except OSError as error:
        raise ValueError("staged artifact is unavailable") from error
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
        or info.st_size > 1024 * 1024
    ):
        raise ValueError("staged artifact is unsafe")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("staged artifact identity changed")
        chunks = bytearray()
        while len(chunks) <= 1024 * 1024:
            chunk = os.read(descriptor, min(65536, 1024 * 1024 + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        raw = bytes(chunks)
    finally:
        os.close(descriptor)
    if len(raw) > 1024 * 1024:
        raise ValueError("staged artifact is too large")
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("staged artifact is invalid") from error


def verify_native_harness_generation(
    generation: str | Path,
) -> Mapping[str, NativeHarnessArtifact]:
    """Read back every file in one content-addressed staging generation."""

    selected = Path(generation)
    if not selected.is_absolute() or DIGEST.fullmatch(selected.name) is None:
        raise ValueError("staged generation path is invalid")
    for directory in (selected.parent, selected):
        try:
            info = directory.lstat()
        except OSError as error:
            raise ValueError("staged generation is unavailable") from error
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise ValueError("staged generation is unsafe")
    manifest = _read_staged_json(selected / "manifest.json")
    if (
        not isinstance(manifest, Mapping)
        or set(manifest) != {"schema_version", "artifacts"}
        or manifest.get("schema_version") != 1
        or not isinstance(manifest.get("artifacts"), Mapping)
        or not manifest["artifacts"]
        or set(manifest["artifacts"]) - SUPPORTED_HARNESSES
        or digest(manifest) != selected.name
    ):
        raise ValueError("staged generation manifest is invalid")
    expected_root_entries = {"manifest.json", *manifest["artifacts"]}
    if {entry.name for entry in selected.iterdir()} != expected_root_entries:
        raise ValueError("staged generation contains unexpected entries")
    verified: dict[str, NativeHarnessArtifact] = {}
    expected_files = {"hooks.json", "settings.json", "tools.json", "artifact.json"}
    for harness_id, receipt in manifest["artifacts"].items():
        harness_dir = selected / harness_id
        try:
            info = harness_dir.lstat()
            entries = {entry.name for entry in harness_dir.iterdir()}
        except OSError as error:
            raise ValueError("staged harness directory is invalid") from error
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or entries != expected_files
        ):
            raise ValueError("staged harness directory is invalid")
        hooks = _read_staged_json(harness_dir / "hooks.json")
        settings = _read_staged_json(harness_dir / "settings.json")
        tools = _read_staged_json(harness_dir / "tools.json")
        artifact_receipt = _read_staged_json(harness_dir / "artifact.json")
        rendered = {
            "schemaVersion": 1,
            "controllerOwned": True,
            "active": False,
            "hooks": hooks,
            "settings": settings,
            "tools": tools,
        }
        artifact = NativeHarnessArtifact(harness_id, rendered, digest(rendered))
        if artifact.to_dict() != artifact_receipt or artifact_receipt != receipt:
            raise ValueError("staged harness artifact digest is invalid")
        verified[harness_id] = artifact
    return MappingProxyType(verified)


def stage_native_harness_artifacts(
    staging_root: str | Path,
    artifacts: Mapping[str, NativeHarnessArtifact],
) -> Path:
    """Publish one immutable content-addressed native harness staging tree."""

    root = Path(staging_root)
    try:
        root_info = root.lstat()
    except OSError as error:
        raise ValueError("staging root is unavailable") from error
    if (
        not root.is_absolute()
        or not stat.S_ISDIR(root_info.st_mode)
        or root_info.st_uid != os.geteuid()
        or root_info.st_mode & 0o077
    ):
        raise ValueError("staging root must be a private current-user directory")
    if (
        not isinstance(artifacts, Mapping)
        or not artifacts
        or set(artifacts) - SUPPORTED_HARNESSES
        or any(
            not isinstance(artifact, NativeHarnessArtifact)
            or artifact.harness_id != harness_id
            for harness_id, artifact in artifacts.items()
        )
    ):
        raise ValueError("native harness artifacts are invalid")
    manifest = {
        "schema_version": 1,
        "artifacts": {
            harness_id: artifacts[harness_id].to_dict()
            for harness_id in sorted(artifacts)
        },
    }
    tree_digest = digest(manifest)
    destination = root / tree_digest
    if destination.exists():
        existing = verify_native_harness_generation(destination)
        if any(
            deep_thaw(existing[harness_id].rendered)
            != deep_thaw(artifacts[harness_id].rendered)
            for harness_id in artifacts
        ):
            raise ValueError("existing staging tree digest conflicts")
        return destination
    temporary = root / f".{tree_digest}.{os.getpid()}.{os.urandom(8).hex()}"
    temporary.mkdir(mode=0o700)
    try:
        for harness_id in sorted(artifacts):
            artifact = artifacts[harness_id]
            harness_dir = temporary / harness_id
            harness_dir.mkdir(mode=0o700)
            rendered = deep_thaw(artifact.rendered)
            for filename, value in (
                ("hooks.json", rendered["hooks"]),
                ("settings.json", rendered["settings"]),
                ("tools.json", rendered["tools"]),
                ("artifact.json", artifact.to_dict()),
            ):
                _write_private_json(harness_dir / filename, value)
            directory_descriptor = os.open(harness_dir, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        _write_private_json(temporary / "manifest.json", manifest)
        directory_descriptor = os.open(temporary, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        try:
            os.rename(temporary, destination)
        except OSError:
            if not destination.exists():
                raise
            _remove_staging_directory(temporary)
            temporary.rmdir()
            return stage_native_harness_artifacts(root, artifacts)
        root_descriptor = os.open(root, os.O_RDONLY)
        try:
            os.fsync(root_descriptor)
        finally:
            os.close(root_descriptor)
    except Exception:
        try:
            _remove_staging_directory(temporary)
            temporary.rmdir()
        except OSError:
            pass
        raise
    return destination


def _hook_commands(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        commands = []
        command = value.get("command")
        if isinstance(command, str):
            commands.append(command)
        for key, child in value.items():
            if key != "command":
                commands.extend(_hook_commands(child))
        return commands
    if isinstance(value, list):
        commands = []
        for child in value:
            commands.extend(_hook_commands(child))
        return commands
    return []


def _integration_roots(values: tuple[str | Path, ...]) -> tuple[str, ...]:
    roots = []
    for value in values:
        selected = Path(value)
        if not selected.is_absolute() or "\x00" in str(selected):
            raise ValueError("upstream integration root must be absolute")
        try:
            canonical = selected.resolve(strict=True)
            metadata = canonical.stat()
            scripts_metadata = (canonical / "scripts").stat()
        except OSError as error:
            raise ValueError("upstream integration root is unavailable") from error
        if (
            canonical == Path(canonical.anchor)
            or not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or not stat.S_ISDIR(scripts_metadata.st_mode)
            or stat.S_IMODE(scripts_metadata.st_mode) & 0o022
        ):
            raise ValueError("upstream integration root is unsafe")
        roots.append(str(canonical).replace("\\", "/"))
    if len(roots) != len(set(roots)):
        raise ValueError("upstream integration roots must be unique")
    return tuple(sorted(roots))


def _is_upstream_hindsight_hook(
    harness_id: str, entry: Any, integration_roots: tuple[str, ...] = ()
) -> bool:
    """Recognize only the verified direct-hook command families we retire."""

    commands = _hook_commands(entry)
    if not commands:
        return False
    for command in commands:
        script_names = [name for name in UPSTREAM_HINDSIGHT_SCRIPTS if name in command]
        if len(script_names) != 1:
            return False
        script = script_names[0]
        if harness_id == "claude-code":
            expected = (
                f'python3 "${{CLAUDE_PLUGIN_ROOT}}/scripts/{script}" || '
                f'python "${{CLAUDE_PLUGIN_ROOT}}/scripts/{script}"'
            )
            if command == expected:
                continue
        if harness_id in {"codex", "cursor"} and command == (
            f'python3 "__SCRIPTS_DIR__/{script}"'
        ):
            continue
        try:
            arguments = shlex.split(command, posix=True)
        except ValueError:
            return False
        if len(arguments) == 2 and arguments[0] in {"python", "python3"}:
            candidate = Path(arguments[1])
            if candidate.is_absolute():
                for root in integration_roots:
                    expected = Path(root) / "scripts" / script
                    try:
                        resolved_candidate = candidate.resolve(strict=True)
                        metadata = expected.lstat()
                    except OSError:
                        continue
                    if (
                        resolved_candidate == expected
                        and stat.S_ISREG(metadata.st_mode)
                        and not stat.S_IMODE(metadata.st_mode) & 0o022
                    ):
                        break
                else:
                    return False
                continue
        return False
    return True


def _native_target(
    artifact: NativeHarnessArtifact,
    current: Mapping[str, Any],
    integration_roots: tuple[str, ...] = (),
) -> dict[str, Any]:
    if not isinstance(current, Mapping):
        raise ValueError("current native harness configuration must be an object")
    rendered = deep_thaw(artifact.rendered)
    target = deep_thaw(deep_freeze(current))
    for surface in ("hooks", "settings", "tools"):
        if surface in target and not isinstance(target[surface], Mapping):
            raise ValueError(f"current native {surface} surface must be an object")

    current_hooks = deep_thaw(target.get("hooks", {}))
    artifact_hooks = deep_thaw(rendered["hooks"])
    for key, value in artifact_hooks.items():
        if key != "hooks":
            current_hooks[key] = value
    current_events = deep_thaw(current_hooks.get("hooks", {}))
    if not isinstance(current_events, Mapping):
        raise ValueError("current native hook events must be an object")
    filtered_events: dict[str, Any] = {}
    for event, entries in current_events.items():
        if not isinstance(entries, list):
            raise ValueError("current native hook registration must be a list")
        retained = [
            entry
            for entry in entries
            if not _is_upstream_hindsight_hook(
                artifact.harness_id, entry, integration_roots
            )
        ]
        if retained:
            filtered_events[event] = retained
    current_events = filtered_events
    for event, entries in artifact_hooks["hooks"].items():
        existing = deep_thaw(current_events.get(event, []))
        if not isinstance(existing, list):
            raise ValueError("current native hook registration must be a list")
        for entry in entries:
            if entry not in existing:
                existing.append(deep_thaw(entry))
        current_events[event] = existing
    current_hooks["hooks"] = current_events
    target["hooks"] = current_hooks

    settings = deep_thaw(target.get("settings", {}))
    for key in RETIRED_DIRECT_KEYS:
        settings.pop(key, None)
    settings.update(deep_thaw(rendered["settings"]))
    if (
        type(settings.get("schemaVersion")) is int
        and settings["schemaVersion"] == 1
        and isinstance(settings.get("broker"), Mapping)
        and isinstance(settings.get("adapter"), str)
        and settings.get("active") is False
    ):
        settings["active"] = True
    target["settings"] = settings
    tools = deep_thaw(target.get("tools", {}))
    tools.update(deep_thaw(rendered["tools"]))
    target["tools"] = tools
    return target


def _retired_direct_hooks(
    artifact: NativeHarnessArtifact,
    current: Mapping[str, Any],
    integration_roots: tuple[str, ...] = (),
) -> dict[str, list[Any]]:
    hooks = current.get("hooks", {})
    if not isinstance(hooks, Mapping):
        return {}
    events = hooks.get("hooks", {})
    if not isinstance(events, Mapping):
        return {}
    return {
        event: [
            deep_thaw(entry)
            for entry in entries
            if _is_upstream_hindsight_hook(
                artifact.harness_id, entry, integration_roots
            )
        ]
        for event, entries in events.items()
        if isinstance(entries, list)
        and any(
            _is_upstream_hindsight_hook(
                artifact.harness_id, entry, integration_roots
            )
            for entry in entries
        )
    }


def native_activation_plan(
    artifact: NativeHarnessArtifact,
    current: Mapping[str, Any],
    *,
    staged_generation: str | Path,
    upstream_integration_roots: tuple[str | Path, ...] = (),
    inventory_digest: str,
    artifact_digest: str,
    policy_digest: str,
) -> NativeActivationPlan:
    """Bind semantic native activation to exact staged and active digests."""

    if not isinstance(artifact, NativeHarnessArtifact):
        raise ValueError("native harness artifact is required")
    verified_generation = verify_native_harness_generation(staged_generation)
    verified_artifact = verified_generation.get(artifact.harness_id)
    if (
        verified_artifact is None
        or verified_artifact.artifact_digest != artifact.artifact_digest
        or deep_thaw(verified_artifact.rendered) != deep_thaw(artifact.rendered)
    ):
        raise ValueError("native artifact is not in the staged generation")
    staged_generation_digest = Path(staged_generation).name
    integration_roots = _integration_roots(upstream_integration_roots)
    for value, label in (
        (inventory_digest, "inventory digest"),
        (artifact_digest, "artifact digest"),
        (policy_digest, "policy digest"),
        (artifact.artifact_digest, "native artifact digest"),
    ):
        _validate_digest(value, label)
    prestate = deep_thaw(deep_freeze(current))
    target = _native_target(artifact, current, integration_roots)
    retired_direct_hooks_digest = digest(
        _retired_direct_hooks(artifact, current, integration_roots)
    )
    upstream_integration_roots_digest = digest(list(integration_roots))
    body = {
        "schema_version": 1,
        "harness_id": artifact.harness_id,
        "inventory_digest": inventory_digest,
        "artifact_digest": artifact_digest,
        "policy_digest": policy_digest,
        "native_artifact_digest": artifact.artifact_digest,
        "staged_generation_digest": staged_generation_digest,
        "upstream_integration_roots_digest": upstream_integration_roots_digest,
        "retired_direct_hooks_digest": retired_direct_hooks_digest,
        "expected_prestate_digest": digest(prestate),
        "target_digest": digest(target),
    }
    return NativeActivationPlan(
        artifact.harness_id,
        inventory_digest,
        artifact_digest,
        policy_digest,
        artifact.artifact_digest,
        staged_generation_digest,
        upstream_integration_roots_digest,
        retired_direct_hooks_digest,
        body["expected_prestate_digest"],
        body["target_digest"],
        prestate,
        target,
        digest(body),
    )


def _valid_native_plan(plan: Any) -> bool:
    if not isinstance(plan, NativeActivationPlan):
        return False
    if plan.harness_id not in SUPPORTED_HARNESSES:
        return False
    try:
        for value, label in (
            (plan.inventory_digest, "inventory digest"),
            (plan.artifact_digest, "artifact digest"),
            (plan.policy_digest, "policy digest"),
            (plan.native_artifact_digest, "native artifact digest"),
            (plan.staged_generation_digest, "staged generation digest"),
            (
                plan.upstream_integration_roots_digest,
                "upstream integration roots digest",
            ),
            (plan.retired_direct_hooks_digest, "retired direct hooks digest"),
            (plan.expected_prestate_digest, "prestate digest"),
            (plan.target_digest, "target digest"),
            (plan.plan_digest, "plan digest"),
        ):
            _validate_digest(value, label)
        return (
            digest(plan.prestate) == plan.expected_prestate_digest
            and digest(plan.target) == plan.target_digest
            and digest(plan.body()) == plan.plan_digest
        )
    except (TypeError, ValueError):
        return False


def load_native_activation_receipt(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Load a secret-free digest receipt for a reconstructable native plan."""

    expected = {
        "schema_version",
        "harness_id",
        "inventory_digest",
        "artifact_digest",
        "policy_digest",
        "native_artifact_digest",
        "staged_generation_digest",
        "upstream_integration_roots_digest",
        "retired_direct_hooks_digest",
        "expected_prestate_digest",
        "target_digest",
        "plan_digest",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError("native activation receipt is invalid")
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("native activation receipt is invalid")
    if (
        not isinstance(value.get("harness_id"), str)
        or value["harness_id"] not in SUPPORTED_HARNESSES
    ):
        raise ValueError("native activation receipt is invalid")
    for key in expected - {"schema_version", "harness_id"}:
        _validate_digest(value[key], key.replace("_", " "))
    body = {key: deep_thaw(value[key]) for key in expected - {"plan_digest"}}
    if not hmac.compare_digest(digest(body), value["plan_digest"]):
        raise ValueError("native activation receipt is invalid")
    return deep_freeze(deep_thaw(value))


def apply_native_activation(
    plan: NativeActivationPlan,
    current: Mapping[str, Any],
    *,
    staged_generation: str | Path,
    approved_plan_digest: str,
    inventory_digest: str,
    artifact_digest: str,
    policy_digest: str,
    broker_healthy: bool,
    profile_healthy: bool,
    adapter_self_test: bool,
    persist_rollback: Callable[[Mapping[str, Any]], None],
    read_rollback: Callable[[], Mapping[str, Any]],
    read_configuration: Callable[[], Mapping[str, Any]],
    write_configuration: Callable[[str, Mapping[str, Any]], Any],
    postcheck: Callable[[str, str, str], bool],
) -> NativeActivationOutcome:
    """Apply an exact native target with CAS and verified automatic rollback."""

    def outcome(
        status: str,
        reason: str,
        configuration: Mapping[str, Any],
        *,
        rollback_attempted: bool = False,
        rollback_succeeded: bool = False,
    ) -> NativeActivationOutcome:
        return NativeActivationOutcome(
            status,
            reason,
            deep_thaw(deep_freeze(configuration)),
            plan.plan_digest if isinstance(plan, NativeActivationPlan) else "",
            rollback_attempted,
            rollback_succeeded,
        )

    if not isinstance(current, Mapping) or not _valid_native_plan(plan):
        return outcome("refused", "invalid_plan", current if isinstance(current, Mapping) else {})
    try:
        staged_artifacts = verify_native_harness_generation(staged_generation)
    except (OSError, ValueError, TypeError):
        return outcome("refused", "staged_generation_invalid", current)
    staged_artifact = staged_artifacts.get(plan.harness_id)
    if (
        Path(staged_generation).name != plan.staged_generation_digest
        or staged_artifact is None
        or staged_artifact.artifact_digest != plan.native_artifact_digest
    ):
        return outcome("refused", "staged_generation_changed", current)
    if not isinstance(approved_plan_digest, str) or not hmac.compare_digest(
        approved_plan_digest, plan.plan_digest
    ):
        return outcome("refused", "plan_not_approved", current)
    for label, actual, expected in (
        ("inventory", inventory_digest, plan.inventory_digest),
        ("artifact", artifact_digest, plan.artifact_digest),
        ("policy", policy_digest, plan.policy_digest),
    ):
        if not isinstance(actual, str) or not hmac.compare_digest(actual, expected):
            return outcome("refused", f"{label}_digest_changed", current)
    for healthy, reason in (
        (broker_healthy, "broker_unhealthy"),
        (profile_healthy, "profile_unhealthy"),
        (adapter_self_test, "adapter_self_test_failed"),
    ):
        if healthy is not True:
            return outcome("refused", reason, current)
    if not hmac.compare_digest(digest(current), plan.expected_prestate_digest):
        return outcome("refused", "prestate_changed", current)
    if not all(
        callable(callback)
        for callback in (
            persist_rollback,
            read_rollback,
            read_configuration,
            write_configuration,
            postcheck,
        )
    ):
        return outcome("refused", "activation_io_unavailable", current)
    try:
        persist_rollback(deep_thaw(plan.prestate))
        persisted_rollback = read_rollback()
        observed = read_configuration()
    except Exception:
        return outcome("refused", "prestate_persistence_failed", current)
    if (
        not isinstance(persisted_rollback, Mapping)
        or digest(persisted_rollback) != plan.expected_prestate_digest
    ):
        return outcome("refused", "prestate_persistence_failed", current)
    if not isinstance(observed, Mapping) or digest(observed) != plan.expected_prestate_digest:
        return outcome("refused", "prestate_changed", current)
    try:
        write_configuration(plan.expected_prestate_digest, deep_thaw(plan.target))
        activated = read_configuration()
    except Exception:
        activated = current
        reason = "activation_write_failed"
    else:
        if not isinstance(activated, Mapping) or digest(activated) != plan.target_digest:
            reason = "activation_readback_mismatch"
        else:
            try:
                postcheck_passed = (
                    postcheck(plan.plan_digest, plan.target_digest, plan.harness_id)
                    is True
                )
            except Exception:
                postcheck_passed = False
            if postcheck_passed:
                try:
                    final_readback = read_configuration()
                except Exception:
                    final_readback = None
                if (
                    isinstance(final_readback, Mapping)
                    and digest(final_readback) == plan.target_digest
                ):
                    return outcome("activated", "ok", final_readback)
                activated = (
                    final_readback
                    if isinstance(final_readback, Mapping)
                    else activated
                )
                reason = "postcheck_drift"
            else:
                reason = "postcheck_failed"
    rollback_current: Mapping[str, Any] | None = None
    try:
        rollback_current = read_configuration()
        if not isinstance(rollback_current, Mapping):
            raise ValueError("rollback state unavailable")
        rollback_target = _native_rollback_target(
            rollback_current, plan.prestate, plan.target
        )
        write_configuration(digest(rollback_current), rollback_target)
        restored = read_configuration()
        succeeded = (
            isinstance(restored, Mapping)
            and digest(restored) == digest(rollback_target)
        )
    except Exception:
        restored = (
            rollback_current
            if isinstance(rollback_current, Mapping)
            else activated if isinstance(activated, Mapping) else current
        )
        succeeded = False
    return outcome(
        "rolled_back" if succeeded else "rollback_failed",
        reason,
        restored,
        rollback_attempted=True,
        rollback_succeeded=succeeded,
    )


def _native_rollback_target(
    observed: Mapping[str, Any],
    prestate: Mapping[str, Any],
    target: Mapping[str, Any],
) -> dict[str, Any]:
    """Remove only activation-owned native changes from a fresh observation."""

    restored = deep_thaw(deep_freeze(observed))
    before = deep_thaw(prestate)
    activated = deep_thaw(target)
    missing = object()
    for surface in ("settings", "tools"):
        before_surface = before.get(surface, {})
        target_surface = activated.get(surface, {})
        observed_surface = restored.setdefault(surface, {})
        if not all(
            isinstance(value, Mapping)
            for value in (before_surface, target_surface, observed_surface)
        ):
            raise ValueError("native activation surface changed")
        for key in set(before_surface) | set(target_surface):
            before_value = before_surface.get(key, missing)
            target_value = target_surface.get(key, missing)
            observed_value = observed_surface.get(key, missing)
            if before_value == target_value:
                continue
            if observed_value == before_value:
                continue
            if observed_value != target_value:
                raise ValueError("activation-owned native setting changed")
            if key in before_surface:
                observed_surface[key] = deep_thaw(before_surface[key])
            else:
                observed_surface.pop(key, None)

    before_hooks = before.get("hooks", {})
    target_hooks = activated.get("hooks", {})
    observed_hooks = restored.setdefault("hooks", {})
    if not all(
        isinstance(value, Mapping)
        for value in (before_hooks, target_hooks, observed_hooks)
    ):
        raise ValueError("native hook surface changed")
    for key in set(before_hooks) | set(target_hooks):
        if key == "hooks":
            continue
        before_value = before_hooks.get(key, missing)
        target_value = target_hooks.get(key, missing)
        observed_value = observed_hooks.get(key, missing)
        if before_value == target_value:
            continue
        if observed_value == before_value:
            continue
        if observed_value != target_value:
            raise ValueError("activation-owned native hook metadata changed")
        if key in before_hooks:
            observed_hooks[key] = deep_thaw(before_hooks[key])
        else:
            observed_hooks.pop(key, None)
    before_events = before_hooks.get("hooks", {})
    target_events = target_hooks.get("hooks", {})
    observed_events = observed_hooks.setdefault("hooks", {})
    if not all(
        isinstance(value, Mapping)
        for value in (before_events, target_events, observed_events)
    ):
        raise ValueError("native hook events changed")
    for event in set(before_events) | set(target_events):
        target_entries = target_events.get(event, [])
        prior_entries = before_events.get(event, [])
        current_entries = observed_events.get(event, [])
        if not all(isinstance(value, list) for value in (prior_entries, target_entries, current_entries)):
            raise ValueError("native hook registration changed")
        if current_entries == prior_entries:
            continue
        if current_entries == target_entries:
            if prior_entries:
                observed_events[event] = deep_thaw(prior_entries)
            else:
                observed_events.pop(event, None)
            continue
        current_entries = deep_thaw(current_entries)

        def count_equal(entries: list[Any], selected: Any) -> int:
            return sum(entry == selected for entry in entries)

        representatives: list[Any] = []
        for entry in [*prior_entries, *target_entries]:
            if not any(entry == selected for selected in representatives):
                representatives.append(entry)

        for entry in representatives:
            addition_count = max(
                0,
                count_equal(target_entries, entry)
                - count_equal(prior_entries, entry),
            )
            while addition_count and entry in current_entries:
                reverse_index = next(
                    index
                    for index in range(len(current_entries) - 1, -1, -1)
                    if current_entries[index] == entry
                )
                current_entries.pop(reverse_index)
                addition_count -= 1

        missing_budget: list[list[Any]] = []
        for entry in representatives:
            prior_count = count_equal(prior_entries, entry)
            target_count = count_equal(target_entries, entry)
            current_count = count_equal(current_entries, entry)
            missing_count = max(0, prior_count - current_count)
            activation_removals = max(0, prior_count - target_count)
            if missing_count > activation_removals:
                raise ValueError("activation-owned native hook changed")
            missing_budget.append([entry, missing_count])

        cursor = 0
        for entry in prior_entries:
            matching_index = next(
                (
                    index
                    for index in range(cursor, len(current_entries))
                    if current_entries[index] == entry
                ),
                None,
            )
            if matching_index is not None:
                cursor = matching_index + 1
                continue
            budget = next(
                item for item in missing_budget if item[0] == entry
            )
            if budget[1] <= 0:
                raise ValueError("activation-owned native hook changed")
            current_entries.insert(cursor, deep_thaw(entry))
            budget[1] -= 1
            cursor += 1
        if current_entries:
            observed_events[event] = current_entries
        else:
            observed_events.pop(event, None)
    return restored


def native_rollback_target(
    plan: NativeActivationPlan,
    current: Mapping[str, Any],
    *,
    approved_plan_digest: str,
) -> Mapping[str, Any]:
    """Prepare an owned-field rollback while preserving later unrelated edits."""

    if not _valid_native_plan(plan):
        raise ValueError("native activation plan is invalid")
    if not isinstance(approved_plan_digest, str) or not hmac.compare_digest(
        approved_plan_digest, plan.plan_digest
    ):
        raise ValueError("native activation plan is not approved")
    if not isinstance(current, Mapping):
        raise ValueError("current native harness configuration must be an object")
    return deep_freeze(_native_rollback_target(current, plan.prestate, plan.target))


def _validate_digest(value: str, label: str) -> str:
    if not isinstance(value, str) or DIGEST.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _owned_prestate(configuration: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        key: (
            {"present": True, "value": deep_thaw(configuration[key])}
            if key in configuration
            else {"present": False}
        )
        for key in OWNED_KEYS
    }


def _activation_surface(
    configuration: Mapping[str, Any], retired_keys: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    keys = OWNED_KEYS.union(retired_keys)
    return {
        key: (
            {"present": True, "value": deep_thaw(configuration[key])}
            if key in configuration
            else {"present": False}
        )
        for key in keys
    }


def _validate_prestate(value: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != OWNED_KEYS:
        raise ValueError("owned prestate must contain the exact owned-key set")
    result: dict[str, dict[str, Any]] = {}
    for key in OWNED_KEYS:
        record = value[key]
        if not isinstance(record, Mapping) or record.get("present") not in {True, False}:
            raise ValueError("owned prestate entry is invalid")
        expected = {"present", "value"} if record["present"] is True else {"present"}
        if set(record) != expected:
            raise ValueError("owned prestate entry is invalid")
        result[key] = deep_thaw(record)
    return result


def activation_plan(
    rendered: RenderedHarness,
    *,
    inventory_digest: str,
    artifact_digest: str,
    policy_digest: str,
    current: Mapping[str, Any] | None = None,
) -> ActivationPlan:
    """Build an immutable activation plan without activating the harness."""

    if not isinstance(rendered, RenderedHarness):
        raise ValueError("rendered harness is required")
    digests = {
        "inventory_digest": _validate_digest(inventory_digest, "inventory digest"),
        "artifact_digest": _validate_digest(artifact_digest, "artifact digest"),
        "policy_digest": _validate_digest(policy_digest, "policy digest"),
    }
    if current is None:
        prestate = _validate_prestate(rendered.prestate)
        expected_prestate_digest = rendered.expected_prestate_digest
    else:
        if not isinstance(current, Mapping):
            raise ValueError("current harness configuration must be an object")
        expected_prestate_digest = digest(current)
        if not hmac.compare_digest(expected_prestate_digest, rendered.expected_prestate_digest):
            raise ValueError("current harness configuration does not match rendered prestate")
        prestate = _owned_prestate(current)
    target = {key: deep_thaw(rendered.rendered[key]) for key in OWNED_KEYS}
    target["active"] = True
    body = {
        "schema_version": 1,
        "harness_id": rendered.harness_id,
        **digests,
        "expected_prestate_digest": expected_prestate_digest,
        "expected_owned_prestate_digest": digest(prestate),
        "owned_target": target,
        "retired_keys": list(rendered.retired_keys),
        "requirements": list(ACTIVATION_REQUIREMENTS),
    }
    return ActivationPlan(
        rendered.harness_id,
        inventory_digest,
        artifact_digest,
        policy_digest,
        expected_prestate_digest,
        body["expected_owned_prestate_digest"],
        target,
        rendered.retired_keys,
        ACTIVATION_REQUIREMENTS,
        digest(body),
    )


def _valid_plan(plan: ActivationPlan) -> bool:
    if not isinstance(plan, ActivationPlan):
        return False
    if plan.harness_id not in SUPPORTED_HARNESSES:
        return False
    try:
        _validate_digest(plan.inventory_digest, "inventory digest")
        _validate_digest(plan.artifact_digest, "artifact digest")
        _validate_digest(plan.policy_digest, "policy digest")
        _validate_digest(plan.expected_prestate_digest, "prestate digest")
        _validate_digest(plan.expected_owned_prestate_digest, "owned prestate digest")
        _validate_digest(plan.plan_digest, "plan digest")
    except (TypeError, ValueError):
        return False
    target = plan.owned_target
    if (
        not isinstance(target, Mapping)
        or set(target) != OWNED_KEYS
        or type(target.get("schemaVersion")) is not int
        or target.get("schemaVersion") != 1
        or target.get("active") is not True
    ):
        return False
    broker = target.get("broker")
    if (
        not isinstance(broker, Mapping)
        or set(broker) != {"transport", "path", "scope"}
        or broker.get("transport") != "unix"
        or broker.get("scope") != "user"
    ):
        return False
    try:
        _socket_path(broker.get("path"))
        _identifier(target.get("adapter"), "adapter identity")
    except (TypeError, ValueError):
        return False
    try:
        if len(set(plan.retired_keys)) != len(plan.retired_keys) or not set(
            plan.retired_keys
        ).issubset(RETIRED_DIRECT_KEYS):
            return False
        if plan.requirements != ACTIVATION_REQUIREMENTS:
            return False
        return hmac.compare_digest(digest(plan.body()), plan.plan_digest)
    except (TypeError, ValueError):
        return False


def _state(configuration: Mapping[str, Any]) -> str:
    active = configuration.get("active") if isinstance(configuration, Mapping) else None
    if active is True:
        return "active"
    if active is False:
        return "inactive"
    return "unknown"


def _outcome(
    status: str,
    reason: str,
    configuration: Mapping[str, Any],
    plan: Any,
    *,
    rollback_attempted: bool = False,
    rollback_succeeded: bool = False,
) -> ActivationOutcome:
    copied = deep_thaw(deep_freeze(configuration))
    return ActivationOutcome(
        status,
        reason,
        copied,
        _state(copied),
        plan.plan_digest
        if isinstance(plan, ActivationPlan)
        and isinstance(plan.plan_digest, str)
        else "",
        rollback_attempted,
        rollback_succeeded,
    )


def _owned_rollback_target(
    observed: Mapping[str, Any], prestate: Mapping[str, Any], plan: ActivationPlan
) -> dict[str, Any]:
    restored = deep_thaw(deep_freeze(observed))
    for key in OWNED_KEYS | set(plan.retired_keys):
        if key in prestate:
            restored[key] = deep_thaw(deep_freeze(prestate[key]))
        else:
            restored.pop(key, None)
    return restored


def apply_activation(
    plan: ActivationPlan,
    current: Mapping[str, Any],
    *,
    approved_plan_digest: str,
    inventory_digest: str,
    artifact_digest: str,
    policy_digest: str,
    broker_healthy: bool,
    profile_healthy: bool,
    adapter_self_test: bool,
    persist_rollback_prestate: Callable[[Mapping[str, Any]], None],
    read_rollback_prestate: Callable[[], Mapping[str, Any]] | None = None,
    write_configuration: Callable[
        [str, Mapping[str, Any]], Mapping[str, Any] | None
    ],
    postcheck: Callable[[str, str, str], bool],
    destination_harness_id: str,
    read_configuration: Callable[[], Mapping[str, Any]] | None = None,
) -> ActivationOutcome:
    """Apply only the plan's owned target after every fresh gate passes."""

    if not isinstance(current, Mapping):
        raise ValueError("current harness configuration must be an object")
    if not _valid_plan(plan):
        return _outcome("refused", "invalid_plan", current, plan)
    if destination_harness_id != plan.harness_id:
        return _outcome("refused", "destination_harness_changed", current, plan)
    if not isinstance(approved_plan_digest, str) or not hmac.compare_digest(
        approved_plan_digest, plan.plan_digest
    ):
        return _outcome("refused", "plan_not_approved", current, plan)
    for label, actual, expected in (
        ("inventory", inventory_digest, plan.inventory_digest),
        ("artifact", artifact_digest, plan.artifact_digest),
        ("policy", policy_digest, plan.policy_digest),
    ):
        if not isinstance(actual, str) or not hmac.compare_digest(actual, expected):
            return _outcome("refused", f"{label}_digest_changed", current, plan)
    for healthy, reason in (
        (broker_healthy, "broker_unhealthy"),
        (profile_healthy, "profile_unhealthy"),
        (adapter_self_test, "adapter_self_test_failed"),
    ):
        if healthy is not True:
            return _outcome("refused", reason, current, plan)
    if not hmac.compare_digest(digest(_owned_prestate(current)), plan.expected_owned_prestate_digest):
        return _outcome("refused", "owned_prestate_changed", current, plan)
    if not hmac.compare_digest(digest(current), plan.expected_prestate_digest):
        return _outcome("refused", "prestate_changed", current, plan)
    if tuple(sorted(RETIRED_DIRECT_KEYS.intersection(current))) != plan.retired_keys:
        return _outcome("refused", "retired_keys_changed", current, plan)
    if (
        not callable(persist_rollback_prestate)
        or not callable(write_configuration)
    ):
        return _outcome("refused", "activation_writer_unavailable", current, plan)
    if not callable(read_configuration):
        return _outcome("refused", "activation_reader_unavailable", current, plan)
    if not callable(read_rollback_prestate):
        return _outcome(
            "refused", "rollback_prestate_reader_unavailable", current, plan
        )
    if not callable(postcheck):
        return _outcome("refused", "activation_postcheck_unavailable", current, plan)

    activated = deep_thaw(deep_freeze(current))
    for key in plan.retired_keys:
        activated.pop(key, None)
    for key in OWNED_KEYS:
        activated[key] = deep_thaw(plan.owned_target[key])

    def rollback_owned(reason: str, fallback: Mapping[str, Any]) -> ActivationOutcome:
        configuration = deep_thaw(deep_freeze(fallback))
        rollback_succeeded = False
        try:
            observed = read_configuration()
            cas_observed = read_configuration()
            if (
                not isinstance(observed, Mapping)
                or not isinstance(cas_observed, Mapping)
                or not hmac.compare_digest(digest(observed), digest(cas_observed))
            ):
                raise ValueError("destination changed before rollback")
            observed_surface = digest(
                _activation_surface(observed, plan.retired_keys)
            )
            activated_surface = digest(
                _activation_surface(activated, plan.retired_keys)
            )
            original_surface = digest(
                _activation_surface(current, plan.retired_keys)
            )
            if not (
                hmac.compare_digest(observed_surface, activated_surface)
                or hmac.compare_digest(observed_surface, original_surface)
            ):
                raise ValueError("activation-owned state changed before rollback")
            target = _owned_rollback_target(observed, current, plan)
            if not hmac.compare_digest(digest(observed), digest(target)):
                write_result = write_configuration(digest(observed), target)
                if write_result is not None and (
                    not isinstance(write_result, Mapping)
                    or not hmac.compare_digest(digest(write_result), digest(target))
                ):
                    raise ValueError("rollback write result is invalid")
            restored = read_configuration()
            if not isinstance(restored, Mapping):
                raise ValueError("persisted destination configuration is invalid")
            configuration = deep_thaw(deep_freeze(restored))
            rollback_succeeded = hmac.compare_digest(
                digest(configuration), digest(target)
            ) and hmac.compare_digest(
                digest(_activation_surface(configuration, plan.retired_keys)),
                digest(_activation_surface(current, plan.retired_keys)),
            )
        except Exception:
            try:
                observed = read_configuration()
                if isinstance(observed, Mapping):
                    configuration = deep_thaw(deep_freeze(observed))
            except Exception:
                pass
        return ActivationOutcome(
            "rolled_back" if rollback_succeeded else "rollback_failed",
            reason,
            configuration,
            _state(configuration),
            plan.plan_digest,
            True,
            rollback_succeeded,
        )
    try:
        persist_rollback_prestate(deep_thaw(deep_freeze(current)))
    except Exception:
        return _outcome("refused", "rollback_prestate_persistence_failed", current, plan)
    try:
        persisted_rollback_prestate = read_rollback_prestate()
    except Exception:
        return _outcome(
            "refused", "rollback_prestate_readback_failed", current, plan
        )
    if not isinstance(persisted_rollback_prestate, Mapping):
        return _outcome(
            "refused", "rollback_prestate_readback_failed", current, plan
        )
    if not hmac.compare_digest(
        digest(persisted_rollback_prestate), plan.expected_prestate_digest
    ):
        return _outcome(
            "refused", "rollback_prestate_readback_mismatch", current, plan
        )
    try:
        persisted_prestate = read_configuration()
    except Exception:
        return _outcome("refused", "activation_readback_failed", current, plan)
    if not isinstance(persisted_prestate, Mapping):
        return _outcome("refused", "activation_readback_failed", current, plan)
    if not hmac.compare_digest(digest(persisted_prestate), digest(current)):
        return _outcome("refused", "prestate_changed", persisted_prestate, plan)
    try:
        write_configuration(
            plan.expected_prestate_digest,
            deep_thaw(deep_freeze(activated)),
        )
    except ActivationCASMismatch:
        try:
            observed = read_configuration()
        except Exception:
            observed = current
        configuration = (
            deep_thaw(deep_freeze(observed))
            if isinstance(observed, Mapping)
            else deep_thaw(deep_freeze(current))
        )
        return _outcome("refused", "prestate_changed", configuration, plan)
    except Exception:
        return rollback_owned("activation_write_failed", activated)
    try:
        persisted_value = read_configuration()
        if not isinstance(persisted_value, Mapping):
            raise ValueError("persisted destination configuration is invalid")
        persisted = deep_thaw(deep_freeze(persisted_value))
    except Exception:
        return rollback_owned("activation_readback_failed", activated)
    if not hmac.compare_digest(
        digest(persisted),
        digest(activated),
    ):
        return rollback_owned("activation_readback_mismatch", persisted)
    try:
        postcheck_passed = callable(postcheck) and postcheck(
            plan.plan_digest,
            digest(persisted),
            destination_harness_id,
        ) is True
    except Exception:
        postcheck_passed = False
    if not postcheck_passed:
        try:
            rollback_current = read_configuration()
        except Exception:
            rollback_current = None
        if not isinstance(rollback_current, Mapping):
            configuration = (
                deep_thaw(deep_freeze(rollback_current))
                if isinstance(rollback_current, Mapping)
                else deep_thaw(deep_freeze(persisted))
            )
            return ActivationOutcome(
                "rollback_failed",
                "postcheck_failed",
                configuration,
                _state(configuration),
                plan.plan_digest,
                True,
                False,
            )
        rolled_back = rollback_activation(
            plan,
            rollback_current,
            approved_plan_digest=approved_plan_digest,
            prestate=current,
            destination_harness_id=destination_harness_id,
        )
        configuration = deep_thaw(deep_freeze(rollback_current))
        rollback_succeeded = False
        if rolled_back.status == "rollback_ready":
            try:
                restored = deep_thaw(rolled_back.configuration)
                write_configuration(digest(rollback_current), restored)
                rollback_persisted = read_configuration()
                if not isinstance(rollback_persisted, Mapping):
                    raise ValueError("persisted destination configuration is invalid")
                configuration = deep_thaw(deep_freeze(rollback_persisted))
                rollback_succeeded = hmac.compare_digest(
                    digest(configuration),
                    digest(restored),
                )
            except Exception:
                try:
                    observed = read_configuration()
                    if isinstance(observed, Mapping):
                        configuration = deep_thaw(deep_freeze(observed))
                except Exception:
                    pass
        return ActivationOutcome(
            "rolled_back" if rollback_succeeded else "rollback_failed",
            "postcheck_failed",
            configuration,
            _state(configuration),
            plan.plan_digest,
            True,
            rollback_succeeded,
        )
    return _outcome("activated", "ok", persisted, plan)


def rollback_activation(
    plan: ActivationPlan,
    current: Mapping[str, Any],
    *,
    approved_plan_digest: str,
    prestate: Mapping[str, Any],
    destination_harness_id: str,
) -> ActivationOutcome:
    """Restore activation-owned fields from a digest-bound prestate snapshot."""

    if not isinstance(current, Mapping) or not isinstance(prestate, Mapping):
        raise ValueError("current and prestate harness configurations must be objects")
    if not _valid_plan(plan):
        return _outcome("refused", "invalid_plan", current, plan)
    if destination_harness_id != plan.harness_id:
        return _outcome("refused", "destination_harness_changed", current, plan)
    if not isinstance(approved_plan_digest, str) or not hmac.compare_digest(
        approved_plan_digest, plan.plan_digest
    ):
        return _outcome("refused", "plan_not_approved", current, plan)
    if not hmac.compare_digest(digest(prestate), plan.expected_prestate_digest):
        return _outcome("refused", "rollback_prestate_changed", current, plan)

    expected_surface = {
        **{
            key: {"present": True, "value": deep_thaw(value)}
            for key, value in plan.owned_target.items()
        },
        **{key: {"present": False} for key in plan.retired_keys},
    }
    if not hmac.compare_digest(
        digest(_activation_surface(current, plan.retired_keys)), digest(expected_surface)
    ):
        return _outcome("refused", "activation_state_changed", current, plan)

    restored = deep_thaw(deep_freeze(current))
    for key in OWNED_KEYS.union(plan.retired_keys):
        if key in prestate:
            restored[key] = deep_thaw(prestate[key])
        else:
            restored.pop(key, None)
    return _outcome(
        "rollback_ready",
        "ok",
        restored,
        plan,
        rollback_attempted=False,
        rollback_succeeded=False,
    )
