#!/usr/bin/env python3
"""Plan one Graphite draft transport and emit an exact publisher handoff."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import urllib.parse
from contextlib import ExitStack
from pathlib import Path
from typing import Any

PUBLISHER_SCRIPTS = Path(__file__).parents[2] / "publishing-reviewable-prs/scripts"
WRITER_SCRIPTS = (
    Path(__file__).parents[2] / "writing-reviewable-pr-descriptions/scripts"
)
for scripts in (PUBLISHER_SCRIPTS, WRITER_SCRIPTS):
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))

from change_navigation.review_input import (  # noqa: E402
    PR_NUMBER_TOKEN,
    ReviewInputError,
    bind_review_input,
    load_review_input,
)
from change_navigation.sensitive_content import suspected_secret_error  # noqa: E402
from publication_receipts import (  # noqa: E402
    creation_transaction_lock,
    prepare_receipt_store,
)
from required_review import build_candidate as build_publication_candidate  # noqa: E402
from reviewable_pr_state import (  # noqa: E402
    ExpectedIdentity,
    PublicationError,
    head_base_matches,
    identity_matches,
    open_prs,
    validate_identity_inputs,
)

SCHEMA_VERSION = 2
READ_TIMEOUT_SECONDS = 30
MUTATION_TIMEOUT_SECONDS = 300
VALIDATION_PR_NUMBER = 2_147_483_647
VALIDATOR = WRITER_SCRIPTS / "validate_change_navigation.py"
UPDATE = PUBLISHER_SCRIPTS / "update_reviewable_pr.py"
AUDIT = PUBLISHER_SCRIPTS / "audit_reviewable_pr.py"
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
OBJECT_ID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
SCP_ENDPOINT_RE = re.compile(
    r"(?:(?P<user>[^/@:\s]+)@)?"
    r"(?P<host>\[[0-9A-Fa-f:.]+\]|[^/:\s]+):"
    r"(?P<path>[^:\s][^\s]*)"
)
GRAPHITE_METADATA_MAX_BYTES = 64 * 1024 * 1024
SUPPORTED_GRAPHITE_VERSION = "1.8.6"
TRUSTED_COMMAND_PATH = "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin"
TRUSTED_SYSTEM_EXECUTABLES = {
    "git": Path("/usr/bin/git"),
    "ssh": Path("/usr/bin/ssh"),
}
TRUSTED_GRAPHITE_EXECUTABLE_ENV = "MERGECRAFT_TRUSTED_GT_EXECUTABLE"
SUBPROCESS_ENV_ALLOWLIST = {
    "ALL_PROXY",
    "COMSPEC",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GRAPHITE_API_TOKEN",
    "GRAPHITE_TOKEN",
    "HOME",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "LOGNAME",
    "NO_PROXY",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SSH_AUTH_SOCK",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USER",
    "XDG_CONFIG_HOME",
    "all_proxy",
    "https_proxy",
    "http_proxy",
    "no_proxy",
}
GRAPHITE_METADATA_TABLES = {
    "branch_metadata": (
        ("branch_name", "text", 1, None, 1),
        ("parent_branch_name", "text", 0, None, 0),
        ("parent_branch_revision", "text", 0, None, 0),
        ("last_submitted_version", "text", 0, None, 0),
        ("state", "text", 0, None, 0),
        ("children", "text", 0, None, 0),
        ("branch_revision", "text", 0, None, 0),
        ("validation_result", "text", 0, None, 0),
        ("parent_head_revision", "text", 0, None, 0),
    ),
    "kysely_migration": (
        ("name", "varchar(255)", 1, None, 1),
        ("timestamp", "varchar(255)", 1, None, 0),
    ),
    "kysely_migration_lock": (
        ("id", "varchar(255)", 1, None, 1),
        ("is_locked", "integer", 1, "0", 0),
    ),
}
GRAPHITE_METADATA_OBJECTS = {
    ("index", "idx_branch_metadata_parent", "branch_metadata"),
    ("table", "branch_metadata", "branch_metadata"),
    ("table", "kysely_migration", "kysely_migration"),
    ("table", "kysely_migration_lock", "kysely_migration_lock"),
}
GRAPHITE_METADATA_MIGRATIONS = (
    "20260211_initial_schema",
    "20260212_add_validation_columns",
    "20260220_add_parent_head_revision",
)
_EMPTY_HOOKS: tempfile.TemporaryDirectory[str] | None = None


class GraphiteTransportError(PublicationError):
    """The requested Graphite transport cannot be bound safely."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_text(value: str) -> str:
    return _sha_bytes(value.encode("utf-8"))


def _exact_keys(value: dict[str, Any], keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise GraphiteTransportError(f"{label} has unsupported or missing fields")


def _parse_strict_json(value: str, label: str) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise GraphiteTransportError(f"duplicate JSON key: {key}")
            result[key] = item
        return result

    def reject_constant(value: str) -> None:
        raise GraphiteTransportError(f"non-finite JSON value: {value}")

    try:
        parsed = json.loads(
            value,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as error:
        raise GraphiteTransportError(f"{label} is not strict JSON") from error
    if not isinstance(parsed, dict):
        raise GraphiteTransportError(f"{label} must be a JSON object")
    return parsed


def _strict_json(path: Path) -> dict[str, Any]:
    if not path.is_absolute():
        raise GraphiteTransportError("request and plan paths must be absolute")
    try:
        value = path.read_text(encoding="utf-8")
    except OSError as error:
        raise GraphiteTransportError(f"cannot read strict JSON: {error}") from error
    return _parse_strict_json(value, "JSON document")


def _write_private_json(path: Path, value: dict[str, Any]) -> None:
    if not path.is_absolute():
        raise GraphiteTransportError("output path must be absolute")
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(_canonical(value) + b"\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary.exists():
                temporary.unlink()
    except OSError as error:
        raise GraphiteTransportError(f"cannot write output: {error}") from error


def _empty_hooks_path() -> Path:
    global _EMPTY_HOOKS
    if _EMPTY_HOOKS is None:
        _EMPTY_HOOKS = tempfile.TemporaryDirectory(
            prefix="mergecraft-graphite-empty-hooks-"
        )
    return Path(_EMPTY_HOOKS.name).resolve()


def _trusted_system_executable(name: str) -> str:
    path = TRUSTED_SYSTEM_EXECUTABLES[name]
    try:
        resolved = path.resolve(strict=True)
        if resolved != path:
            raise OSError("system executable is redirected")
        for parent in (Path("/"), Path("/usr"), Path("/usr/bin")):
            metadata = parent.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                raise OSError("system executable ancestry is mutable")
        metadata = resolved.lstat()
    except OSError as error:
        raise GraphiteTransportError(
            f"trusted {name} executable is unavailable"
        ) from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or stat.S_IMODE(metadata.st_mode) & 0o111 == 0
    ):
        raise GraphiteTransportError(f"trusted {name} executable is unavailable")
    return str(resolved)


def _trusted_graphite_executable(root: Path) -> str:
    configured = os.environ.get(TRUSTED_GRAPHITE_EXECUTABLE_ENV)
    if configured is not None:
        selected = Path(configured)
        if not selected.is_absolute():
            raise GraphiteTransportError("trusted gt executable is unavailable")
    else:
        located = shutil.which("gt", path=TRUSTED_COMMAND_PATH)
        if located is None:
            raise GraphiteTransportError("trusted gt executable is unavailable")
        selected = Path(located)
    try:
        resolved = selected.resolve(strict=True)
        repository = root.resolve(strict=True)
        if resolved == repository or repository in resolved.parents:
            raise OSError("Graphite executable is candidate-controlled")
        for parent in reversed(resolved.parents):
            metadata = parent.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid not in {0, os.geteuid()}
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                raise OSError("Graphite executable ancestry is mutable")
        metadata = resolved.lstat()
    except (OSError, RuntimeError) as error:
        raise GraphiteTransportError("trusted gt executable is unavailable") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or stat.S_IMODE(metadata.st_mode) & 0o111 == 0
    ):
        raise GraphiteTransportError("trusted gt executable is unavailable")
    return str(resolved)


def _bind_executable(arguments: list[str], root: Path) -> list[str]:
    if not arguments:
        raise GraphiteTransportError("command is empty")
    executable = arguments[0]
    if executable in TRUSTED_SYSTEM_EXECUTABLES:
        bound = _trusted_system_executable(executable)
    elif executable == "gt":
        bound = _trusted_graphite_executable(root)
    elif Path(executable).is_absolute():
        bound = executable
    else:
        raise GraphiteTransportError("command executable lacks trusted provenance")
    return [bound, *arguments[1:]]


def _environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if key in SUBPROCESS_ENV_ALLOWLIST
    }
    environment["GH_HOST"] = "github.com"
    environment["GITHUB_HOST"] = "github.com"
    environment.update(
        {
            "GIT_ASKPASS": "false",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_EDITOR": "true",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_PAGER": "cat",
            "GIT_PROTOCOL_FROM_USER": "0",
            "GIT_SEQUENCE_EDITOR": "true",
            "GIT_SSH_COMMAND": (
                f"{_trusted_system_executable('ssh')} "
                "-oBatchMode=yes -oConnectionAttempts=1 "
                f"-oConnectTimeout={READ_TIMEOUT_SECONDS}"
            ),
            "GIT_SSH_VARIANT": "ssh",
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "never",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": TRUSTED_COMMAND_PATH,
            "PYTHONDONTWRITEBYTECODE": "1",
            "SHELL": "/bin/sh",
            "SSH_ASKPASS_REQUIRE": "never",
        }
    )
    closed_config = (
        ("core.hooksPath", str(_empty_hooks_path())),
        ("core.fsmonitor", "false"),
        ("core.attributesFile", os.devnull),
        ("credential.helper", ""),
        ("push.gpgSign", "false"),
        ("commit.gpgSign", "false"),
        ("tag.gpgSign", "false"),
        ("protocol.allow", "never"),
        ("protocol.ext.allow", "never"),
        ("protocol.file.allow", "always"),
        ("protocol.https.allow", "always"),
        ("protocol.ssh.allow", "always"),
        ("gc.auto", "0"),
        ("maintenance.auto", "false"),
    )
    environment["GIT_CONFIG_COUNT"] = str(len(closed_config))
    for index, (key, value) in enumerate(closed_config):
        environment[f"GIT_CONFIG_KEY_{index}"] = key
        environment[f"GIT_CONFIG_VALUE_{index}"] = value
    return environment


def _run_raw(
    arguments: list[str],
    *,
    cwd: Path,
    timeout: int = READ_TIMEOUT_SECONDS,
    allowed: tuple[int, ...] = (),
) -> subprocess.CompletedProcess[str]:
    bound_arguments = _bind_executable(arguments, cwd)
    try:
        result = subprocess.run(
            bound_arguments,
            cwd=cwd,
            env=_environment(),
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as error:
        raise GraphiteTransportError(
            f"command did not complete: {arguments[0]}"
        ) from error
    if result.returncode != 0 and result.returncode not in allowed:
        raise GraphiteTransportError(
            f"command failed with status {result.returncode}: {arguments[0]}"
        )
    return result


def _unsafe_git_config_class(key: str) -> str | None:
    normalized = key.lower()
    exact = {
        "core.alternaterefscommand": "core.alternateRefsCommand",
        "core.askpass": "core.askPass",
        "core.attributesfile": "core.attributesFile",
        "core.fsmonitor": "core.fsmonitor",
        "core.gitproxy": "core.gitProxy",
        "core.hookspath": "core.hooksPath",
        "core.sshcommand": "core.sshCommand",
        "core.worktree": "core.worktree",
        "diff.external": "diff.external",
        "gpg.program": "gpg.program",
        "gpg.ssh.defaultkeycommand": "gpg.ssh.defaultKeyCommand",
        "interactive.difffilter": "interactive.diffFilter",
    }
    if normalized in exact:
        return exact[normalized]
    if normalized.startswith("include.") or normalized.startswith("includeif."):
        return "include*.path"
    patterns = (
        (r"protocol(?:\.[^.]+)?\.allow", "protocol.*.allow"),
        (
            r"filter\..+\.(?:clean|smudge|process)",
            "filter.*.(clean|smudge|process)",
        ),
        (r"hook\..+\.command", "hook.*.command"),
        (r"gpg\..+\.program", "gpg.*.program"),
        (r"credential(?:\..+)?\.helper", "credential.*.helper"),
        (r"diff\..+\.(?:command|textconv)", "diff.*.(command|textconv)"),
        (r"difftool\..+\.cmd", "difftool.*.cmd"),
        (r"merge\..+\.driver", "merge.*.driver"),
        (r"mergetool\..+\.cmd", "mergetool.*.cmd"),
        (
            r"remote\..+\.(?:vcs|uploadpack|receivepack)",
            "remote.*.(vcs|uploadpack|receivepack)",
        ),
        (r"url\..+\.(?:insteadof|pushinsteadof)", "url.*.(insteadOf|pushInsteadOf)"),
        (r"submodule\..+\.update", "submodule.*.update"),
    )
    for pattern, config_class in patterns:
        if re.fullmatch(pattern, normalized):
            return config_class
    return None


def _config_key_inventory(root: Path, *, includes: bool) -> list[tuple[str, str, str]]:
    result = _run_raw(
        [
            "git",
            "config",
            "--null",
            "--show-origin",
            "--show-scope",
            "--includes" if includes else "--no-includes",
            "--name-only",
            "--list",
        ],
        cwd=root,
    )
    fields = result.stdout.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    if len(fields) % 3 != 0:
        raise GraphiteTransportError("Git configuration inventory is malformed")
    return [
        (fields[index], fields[index + 1], fields[index + 2])
        for index in range(0, len(fields), 3)
    ]


def _effective_config_sha256(root: Path) -> str:
    result = _run_raw(
        [
            "git",
            "config",
            "--null",
            "--show-origin",
            "--show-scope",
            "--includes",
            "--list",
        ],
        cwd=root,
    )
    fields = result.stdout.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    if len(fields) % 3 != 0:
        raise GraphiteTransportError("Git configuration inventory is malformed")
    inventory: list[dict[str, str]] = []
    for index in range(0, len(fields), 3):
        scope, origin, key_value = fields[index : index + 3]
        if "\n" not in key_value:
            raise GraphiteTransportError("Git configuration inventory is malformed")
        key, value = key_value.split("\n", 1)
        if scope != "command":
            inventory.append(
                {"scope": scope, "origin": origin, "key": key, "value": value}
            )
    return _sha_bytes(_canonical(inventory))


def _local_remote_path(endpoint: str, root: Path) -> Path | None:
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme == "file":
        if (
            parsed.netloc not in {"", "localhost"}
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise GraphiteTransportError("repository remote transport is unsupported")
        value = urllib.parse.unquote(parsed.path)
        if CONTROL_RE.search(value) or not Path(value).is_absolute():
            raise GraphiteTransportError("repository remote transport is unsupported")
        return Path(value)
    if parsed.scheme:
        return None
    if _is_scp_endpoint(endpoint):
        return None
    path = Path(endpoint)
    if path.is_absolute():
        return path
    if endpoint.startswith("-"):
        raise GraphiteTransportError("repository remote transport is unsupported")
    return root / path


def _require_secure_local_ancestry(path: Path) -> None:
    trusted_owners = {0, os.geteuid()}
    child = path.lstat()
    for parent in path.parents:
        metadata = parent.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in trusted_owners:
            raise GraphiteTransportError(
                "repository local remote is not owner-secure"
            )
        writable = stat.S_IMODE(metadata.st_mode) & 0o022
        sticky_protection = bool(metadata.st_mode & stat.S_ISVTX) and (
            child.st_uid in trusted_owners
        )
        if writable and not sticky_protection:
            raise GraphiteTransportError(
                "repository local remote is not owner-secure"
            )
        child = metadata


def _reject_untrusted_local_symlink_ancestors(path: Path) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:-1]:
        current /= component
        metadata = current.lstat()
        if not stat.S_ISLNK(metadata.st_mode):
            continue
        parent = current.parent.lstat()
        if (
            metadata.st_uid != 0
            or parent.st_uid != 0
            or stat.S_IMODE(parent.st_mode) & 0o022
        ):
            raise GraphiteTransportError(
                "repository local remote is not owner-secure"
            )


def _validate_local_remote(path: Path) -> None:
    try:
        lexical = Path(os.path.abspath(path))
        if lexical.is_symlink():
            raise OSError("symlinked local remote")
        _reject_untrusted_local_symlink_ancestors(lexical)
        resolved = lexical.resolve(strict=True)
        metadata = resolved.lstat()
    except (OSError, RuntimeError, ValueError) as error:
        raise GraphiteTransportError(
            "repository local remote is not owner-secure"
        ) from error
    if resolved.is_symlink() or metadata.st_uid != os.geteuid():
        raise GraphiteTransportError("repository local remote is not owner-secure")
    if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
        raise GraphiteTransportError("repository local remote is not owner-secure")
    if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
        raise GraphiteTransportError("repository local remote is not owner-secure")
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise GraphiteTransportError("repository local remote is not owner-secure")
    _require_secure_local_ancestry(resolved)


def _is_scp_endpoint(endpoint: str) -> bool:
    if "://" in endpoint:
        return False
    match = SCP_ENDPOINT_RE.fullmatch(endpoint)
    if match is None:
        return False
    user = match.group("user")
    host = match.group("host")
    return not host.startswith("-") and (user is None or not user.startswith("-"))


def _validate_remote_endpoint(endpoint: str, root: Path) -> None:
    if not endpoint or CONTROL_RE.search(endpoint):
        raise GraphiteTransportError("repository remote transport is unsupported")
    local = _local_remote_path(endpoint, root)
    if local is not None:
        _validate_local_remote(local)
        return
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme == "https":
        try:
            port = parsed.port
        except ValueError as error:
            raise GraphiteTransportError(
                "repository remote transport is unsupported"
            ) from error
        if (
            not parsed.hostname
            or parsed.hostname.startswith("-")
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or port is not None and not 1 <= port <= 65535
        ):
            raise GraphiteTransportError("repository remote transport is unsupported")
        return
    if parsed.scheme == "ssh":
        try:
            port = parsed.port
        except ValueError as error:
            raise GraphiteTransportError(
                "repository remote transport is unsupported"
            ) from error
        if (
            not parsed.hostname
            or parsed.hostname.startswith("-")
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or port is not None and not 1 <= port <= 65535
        ):
            raise GraphiteTransportError("repository remote transport is unsupported")
        return
    if _is_scp_endpoint(endpoint):
        return
    raise GraphiteTransportError("repository remote transport is unsupported")


def _validate_repository_remotes(root: Path) -> None:
    result = _run_raw(
        [
            "git",
            "config",
            "--null",
            "--includes",
            "--get-regexp",
            r"^remote\..*\.(url|pushurl)$",
        ],
        cwd=root,
        allowed=(1,),
    )
    fields = result.stdout.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    for field in fields:
        if "\n" not in field:
            raise GraphiteTransportError("Git remote inventory is malformed")
        _key, endpoint = field.split("\n", 1)
        _validate_remote_endpoint(endpoint, root)


def _establish_inert_git_policy(root: Path) -> str:
    without_includes = _config_key_inventory(root, includes=False)
    include_classes = {
        config_class
        for scope, _origin, key in without_includes
        if scope != "command"
        if (config_class := _unsafe_git_config_class(key)) == "include*.path"
    }
    if include_classes:
        raise GraphiteTransportError(
            "unsafe repository Git configuration: "
            + ",".join(sorted(include_classes))
        )
    inventory = _config_key_inventory(root, includes=True)
    unsafe_classes = {
        config_class
        for scope, _origin, key in inventory
        if scope != "command"
        if (config_class := _unsafe_git_config_class(key)) is not None
    }
    if unsafe_classes:
        raise GraphiteTransportError(
            "unsafe repository Git configuration: "
            + ",".join(sorted(unsafe_classes))
        )
    _validate_repository_remotes(root)
    return _effective_config_sha256(root)


def _run(
    arguments: list[str], *, cwd: Path, timeout: int = READ_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[str]:
    executable = Path(arguments[0]).name if arguments else ""
    if executable in {"git", "gt"}:
        _establish_inert_git_policy(cwd)
    return _run_raw(arguments, cwd=cwd, timeout=timeout)


def _strict_command_line(arguments: list[str], root: Path, label: str) -> str:
    output = _run(arguments, cwd=root).stdout
    if (
        not output.endswith("\n")
        or "\n" in output[:-1]
        or not output[:-1]
        or CONTROL_RE.search(output[:-1])
    ):
        raise GraphiteTransportError(f"{label} is unavailable")
    return output[:-1]


def _github_repository_for_endpoint(endpoint: str) -> str | None:
    if _is_scp_endpoint(endpoint):
        match = SCP_ENDPOINT_RE.fullmatch(endpoint)
        assert match is not None
        if match.group("host").lower() != "github.com":
            return None
        path = match.group("path")
    else:
        parsed = urllib.parse.urlsplit(endpoint)
        host = (parsed.hostname or "").lower()
        if parsed.scheme == "https" and host == "github.com":
            path = parsed.path.removeprefix("/")
        elif parsed.scheme == "ssh" and host in {"github.com", "ssh.github.com"}:
            path = parsed.path.removeprefix("/")
        else:
            return None
    if path.endswith(".git"):
        path = path[:-4]
    return path if path and path.count("/") == 1 else None


def _graphite_repository_binding(
    root: Path,
    candidates: list[dict[str, Any]],
    target_repository: str,
) -> dict[str, str]:
    version = _strict_command_line(
        ["gt", "--version"],
        root,
        "Graphite version",
    )
    if version != SUPPORTED_GRAPHITE_VERSION:
        raise GraphiteTransportError("unsupported Graphite version")
    target_owner, target_name = target_repository.split("/", 1)
    if (
        _strict_command_line(
            ["gt", "repo", "owner"],
            root,
            "Graphite target repository owner",
        )
        != target_owner
        or _strict_command_line(
            ["gt", "repo", "name"],
            root,
            "Graphite target repository name",
        )
        != target_name
    ):
        raise GraphiteTransportError("Graphite repository binding differs from plan")
    head_repositories = {entry["head_repository"] for entry in candidates}
    if len(head_repositories) != 1:
        raise GraphiteTransportError("Graphite stack spans multiple head repositories")
    head_repository = next(iter(head_repositories))
    remote = _strict_command_line(
        ["gt", "repo", "remote"],
        root,
        "Graphite publication remote",
    )
    if remote.startswith("-") or any(character.isspace() for character in remote):
        raise GraphiteTransportError("Graphite publication remote is invalid")
    fetch_endpoint = _strict_command_line(
        ["git", "remote", "get-url", "--all", "--", remote],
        root,
        "Graphite fetch endpoint",
    )
    push_endpoint = _strict_command_line(
        ["git", "remote", "get-url", "--push", "--all", "--", remote],
        root,
        "Graphite push endpoint",
    )
    for endpoint in (fetch_endpoint, push_endpoint):
        _validate_remote_endpoint(endpoint, root)
        if _github_repository_for_endpoint(endpoint) != head_repository:
            raise GraphiteTransportError(
                "Graphite repository binding differs from plan"
            )
    return {
        "contract": "mergecraft-graphite-repository-binding-v1",
        "graphite_version": version,
        "target_repository": target_repository,
        "head_repository": head_repository,
        "remote_sha256": _sha_text(remote),
        "fetch_endpoint_sha256": _sha_text(fetch_endpoint),
        "push_endpoint_sha256": _sha_text(push_endpoint),
    }


def _graphite_metadata_path(root: Path) -> Path:
    value = _run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=root,
    ).stdout
    if not value.endswith("\n") or "\n" in value[:-1] or CONTROL_RE.search(value[:-1]):
        raise GraphiteTransportError("Git common directory is unreadable")
    common = Path(value[:-1])
    if not common.is_absolute():
        raise GraphiteTransportError("Git common directory is not absolute")
    return common / ".graphite_metadata.db"


def _metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_graphite_metadata(path: Path) -> bytes:
    for suffix in ("-journal", "-wal", "-shm"):
        if os.path.lexists(f"{path}{suffix}"):
            raise GraphiteTransportError(
                "Graphite metadata has an unsupported transactional sidecar"
            )
    flags = os.O_RDONLY
    for name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
        flags |= getattr(os, name, 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise GraphiteTransportError("Graphite metadata is unavailable") from error
    try:
        before = os.fstat(descriptor)
        try:
            parent = path.parent.stat()
        except OSError as error:
            raise GraphiteTransportError(
                "Graphite metadata directory is unavailable"
            ) from error
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) & 0o022
            or parent.st_uid != os.geteuid()
            or stat.S_IMODE(parent.st_mode) & 0o022
            or before.st_size <= 0
            or before.st_size > GRAPHITE_METADATA_MAX_BYTES
        ):
            raise GraphiteTransportError("Graphite metadata is not owner-secure")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise GraphiteTransportError("Graphite metadata changed while read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise GraphiteTransportError("Graphite metadata changed while read")
        after = os.fstat(descriptor)
        if _metadata_identity(after) != _metadata_identity(before):
            raise GraphiteTransportError("Graphite metadata changed while read")
    except OSError as error:
        raise GraphiteTransportError("Graphite metadata could not be read") from error
    finally:
        os.close(descriptor)
    for suffix in ("-journal", "-wal", "-shm"):
        if os.path.lexists(f"{path}{suffix}"):
            raise GraphiteTransportError(
                "Graphite metadata changed while it was read"
            )
    return b"".join(chunks)


def _graphite_schema_contract() -> dict[str, Any]:
    return {
        "objects": [list(item) for item in sorted(GRAPHITE_METADATA_OBJECTS)],
        "tables": {
            table: [list(column) for column in columns]
            for table, columns in sorted(GRAPHITE_METADATA_TABLES.items())
        },
        "migrations": list(GRAPHITE_METADATA_MIGRATIONS),
        "parent_index": [[0, 1, "parent_branch_name"]],
        "migration_lock": ["migration_lock", 0],
    }


def _validate_graphite_schema(connection: sqlite3.Connection) -> None:
    objects = {
        (object_type, name, table)
        for object_type, name, table in connection.execute(
            """
            SELECT type, name, tbl_name
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            """
        )
    }
    if objects != GRAPHITE_METADATA_OBJECTS:
        raise GraphiteTransportError("unsupported Graphite metadata schema")
    for table, expected in GRAPHITE_METADATA_TABLES.items():
        actual = tuple(
            (name, str(column_type).lower(), not_null, default, primary_key)
            for (
                _column_id,
                name,
                column_type,
                not_null,
                default,
                primary_key,
            ) in connection.execute(f'PRAGMA table_info("{table}")')
        )
        if actual != expected:
            raise GraphiteTransportError("unsupported Graphite metadata schema")
    index = list(
        connection.execute('PRAGMA index_info("idx_branch_metadata_parent")')
    )
    if index != [(0, 1, "parent_branch_name")]:
        raise GraphiteTransportError("unsupported Graphite metadata schema")
    migrations = tuple(
        row[0]
        for row in connection.execute(
            "SELECT name FROM kysely_migration ORDER BY name"
        )
    )
    if migrations != GRAPHITE_METADATA_MIGRATIONS:
        raise GraphiteTransportError("unsupported Graphite metadata schema")
    lock = list(connection.execute("SELECT id, is_locked FROM kysely_migration_lock"))
    if lock != [("migration_lock", 0)]:
        raise GraphiteTransportError("unsupported Graphite metadata schema")
    if list(connection.execute("PRAGMA integrity_check")) != [("ok",)]:
        raise GraphiteTransportError("Graphite metadata failed integrity validation")


def _open_bound_graphite_metadata(raw: bytes) -> tuple[sqlite3.Connection, Path]:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="mergecraft-graphite-metadata-", suffix=".db"
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        uri_path = urllib.parse.quote(str(temporary), safe="/")
        connection = sqlite3.connect(
            f"file:{uri_path}?mode=ro&immutable=1",
            uri=True,
        )
        connection.execute("PRAGMA query_only=ON")
        return connection, temporary
    except (OSError, sqlite3.Error) as error:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise GraphiteTransportError("Graphite metadata is unreadable") from error


def _graphite_mutation_inventory(
    root: Path,
    candidates: list[dict[str, Any]],
    current_branch: str,
) -> dict[str, Any]:
    path = _graphite_metadata_path(root)
    raw = _read_graphite_metadata(path)
    connection, temporary = _open_bound_graphite_metadata(raw)
    try:
        _validate_graphite_schema(connection)
        records: dict[str, dict[str, str | None]] = {}
        for branch, parent, revision, validation in connection.execute(
            """
            SELECT branch_name, parent_branch_name, branch_revision,
                   validation_result
            FROM branch_metadata
            """
        ):
            if (
                not isinstance(branch, str)
                or not branch
                or CONTROL_RE.search(branch)
                or parent is not None
                and (
                    not isinstance(parent, str)
                    or not parent
                    or CONTROL_RE.search(parent)
                )
                or not isinstance(revision, str)
                or OBJECT_ID_RE.fullmatch(revision) is None
                or validation is not None
                and not isinstance(validation, str)
                or branch in records
            ):
                raise GraphiteTransportError(
                    "unsupported Graphite metadata contents"
                )
            records[branch] = {
                "parent": parent,
                "revision": revision,
                "validation": validation,
            }
        trunks = [
            branch
            for branch, record in records.items()
            if record["validation"] == "TRUNK"
        ]
        if len(trunks) != 1:
            raise GraphiteTransportError(
                "Graphite metadata does not identify exactly one trunk"
            )
        trunk = trunks[0]
        trunk_record = records[trunk]
        if trunk_record["parent"] is not None:
            raise GraphiteTransportError("Graphite trunk metadata has a parent")
        descendant_first: list[dict[str, str]] = []
        visited: set[str] = set()
        branch = current_branch
        while branch != trunk:
            if branch in visited:
                raise GraphiteTransportError("Graphite metadata contains a cycle")
            visited.add(branch)
            record = records.get(branch)
            if record is None or not isinstance(record["parent"], str):
                raise GraphiteTransportError(
                    "Graphite metadata chain does not reach its trunk"
                )
            descendant_first.append(
                {
                    "branch": branch,
                    "parent": record["parent"],
                    "revision": str(record["revision"]),
                }
            )
            branch = record["parent"]
        branches = list(reversed(descendant_first))
        candidate_branches = [entry["local_branch"] for entry in candidates]
        if [entry["branch"] for entry in branches] != candidate_branches:
            raise GraphiteTransportError(
                "reviewed stack does not exactly match Graphite metadata"
            )
        if candidates[0]["base"] != trunk:
            raise GraphiteTransportError(
                "reviewed stack does not start at the Graphite trunk"
            )
        if trunk_record["revision"] != candidates[0]["base_oid"]:
            raise GraphiteTransportError(
                "Graphite trunk revision differs from the reviewed base"
            )
        for inventory_entry, candidate in zip(branches, candidates, strict=True):
            if inventory_entry["revision"] != candidate["head_oid"]:
                raise GraphiteTransportError(
                    "Graphite branch revision differs from the reviewed candidate"
                )
        return {
            "contract": "mergecraft-graphite-mutation-inventory-v1",
            "metadata_sha256": _sha_bytes(raw),
            "schema_sha256": _sha_bytes(_canonical(_graphite_schema_contract())),
            "trunk": {
                "branch": trunk,
                "revision": trunk_record["revision"],
            },
            "branches": branches,
            "submit_scope": "current-and-downstack-only",
        }
    except sqlite3.Error as error:
        raise GraphiteTransportError("Graphite metadata query failed") from error
    finally:
        connection.close()
        try:
            temporary.unlink()
        except OSError as error:
            raise GraphiteTransportError(
                "Graphite metadata snapshot cleanup failed"
            ) from error


def _read_text(path: Path, label: str) -> str:
    if not path.is_absolute():
        raise GraphiteTransportError(f"{label} path must be absolute")
    try:
        raw = path.read_bytes()
        value = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise GraphiteTransportError(f"cannot read {label}: {error}") from error
    if suspected_secret_error(value) is not None:
        raise GraphiteTransportError(
            f"{label} contains a suspected credential or secret"
        )
    return value


def _read_source(path: Path) -> tuple[bytes, str]:
    if not path.is_absolute():
        raise GraphiteTransportError("body source path must be absolute")
    try:
        raw = path.read_bytes()
        value = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise GraphiteTransportError(f"cannot read body source: {error}") from error
    if suspected_secret_error(value) is not None:
        raise GraphiteTransportError(
            "body source contains a suspected credential or secret"
        )
    return raw, value


def _review_profile(entry: dict[str, Any]) -> tuple[str, Path | None, list[str]]:
    mode = entry["review_mode"]
    bundle_value = entry["review_bundle"]
    specialists = entry["selected_specialists"]
    if mode not in {"required", "not-required"}:
        raise GraphiteTransportError("review_mode must be required or not-required")
    if (
        not isinstance(specialists, list)
        or not all(isinstance(value, str) and value for value in specialists)
        or specialists != sorted(set(specialists))
    ):
        raise GraphiteTransportError(
            "selected_specialists must be an explicit sorted unique string list"
        )
    if mode == "required":
        if not isinstance(bundle_value, str) or not Path(bundle_value).is_absolute():
            raise GraphiteTransportError(
                "required review needs an absolute review_bundle path"
            )
        bundle = Path(bundle_value)
    else:
        if bundle_value is not None:
            raise GraphiteTransportError(
                "not-required review cannot carry a review_bundle path"
            )
        bundle = None
    return mode, bundle, specialists


def _matching_prs(entry: dict[str, Any], repository: str) -> list[dict[str, Any]]:
    return [
        stored
        for stored in open_prs(repository, entry["base"], entry["head"])
        if head_base_matches(
            stored,
            base=entry["base"],
            head=entry["head"],
            head_owner=entry["head_owner"],
            head_repository=entry["head_repository"],
        )
    ]


def _candidate(entry: dict[str, Any], repository: str) -> dict[str, Any]:
    _exact_keys(
        entry,
        {
            "base",
            "base_oid",
            "head",
            "head_oid",
            "head_owner",
            "head_repository",
            "title",
            "body_source",
            "review_input",
            "review_mode",
            "review_bundle",
            "selected_specialists",
        },
        "stack entry",
    )
    body_source = entry["body_source"]
    if not isinstance(body_source, dict):
        raise GraphiteTransportError("stack entry body_source must be an object")
    _exact_keys(body_source, {"mode", "path"}, "stack entry body_source")
    if body_source["mode"] not in {"file", "template"}:
        raise GraphiteTransportError("body_source mode must be file or template")
    if not isinstance(entry["title"], str) or not entry["title"].strip():
        raise GraphiteTransportError("stack entry title must be non-empty")
    if suspected_secret_error(entry["title"]) is not None:
        raise GraphiteTransportError("stack entry title contains a suspected secret")
    validate_identity_inputs(
        repository=repository,
        pr_number=None,
        base=entry["base"],
        base_oid=entry["base_oid"],
        head=entry["head"],
        head_oid=entry["head_oid"],
        head_owner=entry["head_owner"],
        head_repository=entry["head_repository"],
    )
    local_branch = entry["head"].split(":", 1)[1]
    source_path = Path(body_source["path"])
    review_input_path = Path(entry["review_input"])
    _review_profile(entry)
    source_raw, source = _read_source(source_path)
    try:
        if not review_input_path.is_absolute():
            raise GraphiteTransportError("review input path must be absolute")
        review_input_raw = review_input_path.read_bytes()
        review_input = load_review_input(review_input_path)
        if review_input_path.read_bytes() != review_input_raw:
            raise GraphiteTransportError("review input changed while it was read")
    except OSError as error:
        raise GraphiteTransportError(f"cannot read review input: {error}") from error
    except ReviewInputError as error:
        raise GraphiteTransportError(f"invalid review input: {error}") from error
    if body_source["mode"] == "template":
        if PR_NUMBER_TOKEN not in source:
            raise GraphiteTransportError(
                f"new-PR body template must contain {PR_NUMBER_TOKEN}"
            )
        if review_input.pr_number != PR_NUMBER_TOKEN:
            raise GraphiteTransportError(
                "template transport requires a new-PR review input"
            )
        rendered = source.replace(PR_NUMBER_TOKEN, str(VALIDATION_PR_NUMBER))
        template = source
        pr_number = VALIDATION_PR_NUMBER
    else:
        if type(review_input.pr_number) is not int:
            raise GraphiteTransportError(
                "existing transport requires a numbered review input"
            )
        rendered = source
        template = None
        pr_number = review_input.pr_number
    try:
        bind_review_input(
            review_input,
            repository=repository,
            pr_number=pr_number,
            base=entry["base"],
            base_oid=entry["base_oid"],
            head=entry["head"],
            head_oid=entry["head_oid"],
            head_owner=entry["head_owner"],
            head_repository=entry["head_repository"],
            title=entry["title"],
            body=rendered,
            template_body=template,
        )
    except ReviewInputError as error:
        raise GraphiteTransportError(f"review input drift: {error}") from error
    arguments = [
        sys.executable,
        "-B",
        str(VALIDATOR),
        "/dev/stdin",
        "--repository",
        repository,
        "--pr",
        str(pr_number),
        "--title",
        entry["title"],
        "--review-input",
        str(review_input_path),
    ]
    if template is not None:
        arguments.extend(["--template-body", str(source_path)])
    try:
        result = subprocess.run(
            arguments,
            input=rendered,
            text=True,
            capture_output=True,
            check=False,
            timeout=READ_TIMEOUT_SECONDS,
            env=_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GraphiteTransportError("candidate validation did not complete") from error
    if result.returncode != 0:
        raise GraphiteTransportError("candidate body failed canonical validation")
    return {
        **entry,
        "local_branch": local_branch,
        "body_source_sha256": _sha_bytes(source_raw),
        "review_input_raw_sha256": _sha_bytes(review_input_raw),
        "review_input_sha256": review_input.content_sha256,
        "review_input_pr": review_input.pr_number,
    }


def _git_graphite_snapshot(root: Path, current_branch: str) -> dict[str, Any]:
    config_sha256 = _establish_inert_git_policy(root)
    resolved = Path(
        _run(["git", "rev-parse", "--show-toplevel"], cwd=root).stdout.strip()
    ).resolve()
    if resolved != root.resolve():
        raise GraphiteTransportError("repository root does not match Git")
    status = _run(["git", "status", "--porcelain=v1"], cwd=root).stdout
    if status:
        raise GraphiteTransportError("Graphite transport requires a clean worktree")
    branch = _run(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"], cwd=root
    ).stdout.strip()
    if branch != current_branch:
        raise GraphiteTransportError("current branch changed before transport")
    log_short = _run(["gt", "log", "short"], cwd=root).stdout
    trunk = _run(["gt", "trunk"], cwd=root).stdout
    return {
        "repository_root": str(root.resolve()),
        "current_branch": branch,
        "clean_status_sha256": _sha_text(status),
        "git_config_sha256": config_sha256,
        "gt_log_short_sha256": _sha_text(log_short),
        "gt_trunk_sha256": _sha_text(trunk),
    }


def _live_preimage(entry: dict[str, Any], repository: str) -> dict[str, Any] | None:
    matches = _matching_prs(entry, repository)
    mode = entry["body_source"]["mode"]
    if mode == "template":
        if matches:
            raise GraphiteTransportError(
                "new-PR transport found an existing open PR for its head/base"
            )
        return None
    if len(matches) != 1:
        raise GraphiteTransportError(
            "existing transport requires exactly one open PR for its head/base"
        )
    stored = matches[0]
    if stored.get("number") != entry["review_input_pr"]:
        raise GraphiteTransportError("existing PR number differs from review input")
    title = stored.get("title")
    body = stored.get("body")
    is_draft = stored.get("isDraft")
    if (
        not isinstance(title, str)
        or not isinstance(body, str)
        or type(is_draft) is not bool
    ):
        raise GraphiteTransportError("existing PR preimage is unreadable")
    if (
        suspected_secret_error(title) is not None
        or suspected_secret_error(body) is not None
    ):
        raise GraphiteTransportError("existing PR contains a suspected secret")
    try:
        review_input = load_review_input(Path(entry["review_input"]))
        bind_review_input(
            review_input,
            repository=repository,
            pr_number=int(stored["number"]),
            base=entry["base"],
            base_oid=entry["base_oid"],
            head=entry["head"],
            head_oid=entry["head_oid"],
            head_owner=entry["head_owner"],
            head_repository=entry["head_repository"],
            title=entry["title"],
            body=_read_text(Path(entry["body_source"]["path"]), "body source"),
            stored_title=title,
            stored_body=body,
        )
    except ReviewInputError as error:
        raise GraphiteTransportError(f"existing PR baseline drift: {error}") from error
    return {
        "number": stored["number"],
        "url": stored["url"],
        "title_sha256": _sha_text(title),
        "body_sha256": _sha_text(body),
        "is_draft": is_draft,
    }


def build_plan(request: dict[str, Any]) -> dict[str, Any]:
    _exact_keys(
        request,
        {"schema_version", "repository", "repository_root", "current_branch", "stack"},
        "request",
    )
    if request["schema_version"] != SCHEMA_VERSION:
        raise GraphiteTransportError(f"request schema_version must be {SCHEMA_VERSION}")
    if not isinstance(request["repository"], str):
        raise GraphiteTransportError("repository must be OWNER/REPO")
    root = Path(request["repository_root"])
    if not root.is_absolute() or not root.is_dir():
        raise GraphiteTransportError("repository_root must be an absolute directory")
    _establish_inert_git_policy(root)
    if not isinstance(request["current_branch"], str) or not request["current_branch"]:
        raise GraphiteTransportError("current_branch must be non-empty")
    if not isinstance(request["stack"], list) or not request["stack"]:
        raise GraphiteTransportError("stack must be a non-empty list")
    candidates = [_candidate(item, request["repository"]) for item in request["stack"]]
    if candidates[-1]["local_branch"] != request["current_branch"]:
        raise GraphiteTransportError("current branch must be the top stack entry")
    for index, entry in enumerate(candidates):
        local_oid = _run(
            ["git", "rev-parse", f"refs/heads/{entry['local_branch']}^{{commit}}"],
            cwd=root,
        ).stdout.strip()
        if local_oid != entry["head_oid"]:
            raise GraphiteTransportError("local stack head OID differs from request")
        base_oid = _run(
            ["git", "rev-parse", f"refs/heads/{entry['base']}^{{commit}}"],
            cwd=root,
        ).stdout.strip()
        if base_oid != entry["base_oid"]:
            raise GraphiteTransportError("local stack base OID differs from request")
        if index and entry["base"] != candidates[index - 1]["local_branch"]:
            raise GraphiteTransportError("stack entries are not a bottom-to-top chain")
    repository_binding = _graphite_repository_binding(
        root,
        candidates,
        request["repository"],
    )
    snapshot = _git_graphite_snapshot(root, request["current_branch"])
    mutation_inventory = _graphite_mutation_inventory(
        root,
        candidates,
        request["current_branch"],
    )
    mutation_inventory["repository_binding"] = repository_binding
    preimages = [_live_preimage(entry, request["repository"]) for entry in candidates]
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "request": request,
        "candidates": candidates,
        "mutation_inventory": mutation_inventory,
        "snapshot": snapshot,
        "preimages": preimages,
    }
    return {**unsigned, "content_sha256": _sha_bytes(_canonical(unsigned))}


def _load_plan(path: Path) -> dict[str, Any]:
    plan = _strict_json(path)
    _exact_keys(
        plan,
        {
            "schema_version",
            "request",
            "candidates",
            "mutation_inventory",
            "snapshot",
            "preimages",
            "content_sha256",
        },
        "plan",
    )
    supplied = plan["content_sha256"]
    if plan["schema_version"] != SCHEMA_VERSION:
        raise GraphiteTransportError(f"plan schema_version must be {SCHEMA_VERSION}")
    unsigned = dict(plan)
    del unsigned["content_sha256"]
    if not isinstance(supplied, str) or supplied != _sha_bytes(_canonical(unsigned)):
        raise GraphiteTransportError("plan content digest does not match")
    return plan


def _identity(
    entry: dict[str, Any], stored: dict[str, Any], repository: str
) -> ExpectedIdentity:
    number = stored.get("number")
    if type(number) is not int or number <= 0:
        raise GraphiteTransportError("transport PR has an invalid number")
    expected = ExpectedIdentity(
        repository=repository,
        pr_number=number,
        base=entry["base"],
        base_oid=entry["base_oid"],
        head=entry["head"],
        head_oid=entry["head_oid"],
        head_owner=entry["head_owner"],
        head_repository=entry["head_repository"],
    )
    if not identity_matches(stored, expected):
        raise GraphiteTransportError("transport PR identity or pushed OIDs differ")
    return expected


def _common_arguments(expected: ExpectedIdentity, entry: dict[str, Any]) -> list[str]:
    return [
        "--repository",
        expected.repository,
        "--pr",
        str(expected.pr_number),
        "--base",
        expected.base,
        "--base-oid",
        expected.base_oid,
        "--head",
        expected.head,
        "--head-oid",
        expected.head_oid,
        "--head-owner",
        expected.head_owner,
        "--head-repository",
        expected.head_repository,
        "--review-input",
        entry["review_input"],
    ]


def _publisher_arguments(
    expected: ExpectedIdentity, entry: dict[str, Any]
) -> list[str]:
    review_mode, review_bundle, specialists = _review_profile(entry)
    arguments = [
        *_common_arguments(expected, entry),
        "--review-mode",
        review_mode,
        "--selected-specialists",
        _canonical(specialists).decode("utf-8"),
    ]
    if review_bundle is not None:
        arguments.extend(["--review-bundle", str(review_bundle)])
    return arguments


def _target_publication_candidate_sha256(
    *,
    entry: dict[str, Any],
    expected: ExpectedIdentity,
    source_raw: bytes,
    target_body: str,
    final_operation: str,
) -> str:
    review_mode, _review_bundle, specialists = _review_profile(entry)
    if final_operation == "mark-ready":
        body_source_kind = "stored-body"
        candidate_source = target_body.encode("utf-8")
    else:
        body_source_kind = (
            "template" if entry["body_source"]["mode"] == "template" else "body"
        )
        candidate_source = source_raw
    try:
        candidate = build_publication_candidate(
            operation=final_operation,
            repository=expected.repository,
            pr_number=expected.pr_number,
            base=expected.base,
            base_oid=expected.base_oid,
            head=expected.head,
            head_oid=expected.head_oid,
            head_owner=expected.head_owner,
            head_repository=expected.head_repository,
            title=entry["title"],
            body_source_kind=body_source_kind,
            body_source_raw=candidate_source,
            published_body=target_body,
            review_input_path=Path(entry["review_input"]),
            review_mode=review_mode,
            selected_specialists=specialists,
        )
    except PublicationError as error:
        raise GraphiteTransportError(
            f"cannot freeze publication convergence target: {error}"
        ) from error
    return candidate.content_sha256


def _handoff_entry(
    entry: dict[str, Any],
    preimage: dict[str, Any] | None,
    stored: dict[str, Any],
    repository: str,
) -> dict[str, Any]:
    expected = _identity(entry, stored, repository)
    title = stored.get("title")
    body = stored.get("body")
    is_draft = stored.get("isDraft")
    if (
        not isinstance(title, str)
        or not isinstance(body, str)
        or type(is_draft) is not bool
    ):
        raise GraphiteTransportError("transport PR state is unreadable")
    if (
        suspected_secret_error(title) is not None
        or suspected_secret_error(body) is not None
    ):
        raise GraphiteTransportError("transport PR contains a suspected secret")
    if preimage is None and not is_draft:
        raise GraphiteTransportError("new Graphite PR was not created as a draft")
    if preimage is not None and preimage["is_draft"] is True and not is_draft:
        raise GraphiteTransportError("Graphite unexpectedly marked a draft PR ready")
    source_path = Path(entry["body_source"]["path"])
    source_raw, source = _read_source(source_path)
    target_body = (
        source.replace(PR_NUMBER_TOKEN, str(expected.pr_number))
        if entry["body_source"]["mode"] == "template"
        else source
    )
    common = _publisher_arguments(expected, entry)
    audit_common = _common_arguments(expected, entry)
    commands: list[list[str]] = []
    target_is_draft = True if preimage is None else preimage["is_draft"]
    if title != entry["title"] or body != target_body:
        body_flag = (
            "--body-template"
            if entry["body_source"]["mode"] == "template"
            else "--body-file"
        )
        commands.append(
            [
                sys.executable,
                str(UPDATE),
                "text",
                *common,
                "--expected-title-sha256",
                _sha_text(title),
                "--expected-body-sha256",
                _sha_text(body),
                "--expected-state",
                "draft" if is_draft else "ready",
                "--text-scope",
                "title-body",
                "--title",
                entry["title"],
                body_flag,
                str(source_path),
            ]
        )
    if preimage is not None and preimage["is_draft"] is False and is_draft:
        commands.append(
            [
                sys.executable,
                str(UPDATE),
                "ready",
                *common,
                "--expected-title-sha256",
                _sha_text(entry["title"]),
                "--expected-body-sha256",
                _sha_text(target_body),
            ]
        )
    if commands:
        final_operation = "mark-ready" if commands[-1][2] == "ready" else "update-text"
    else:
        final_operation = "update-text" if target_is_draft else "mark-ready"
    target_candidate_sha256 = _target_publication_candidate_sha256(
        entry=entry,
        expected=expected,
        source_raw=source_raw,
        target_body=target_body,
        final_operation=final_operation,
    )
    review_mode, review_bundle, specialists = _review_profile(entry)
    audit = [sys.executable, str(AUDIT), "audit", *audit_common]
    return {
        "repository": repository,
        "pr": expected.pr_number,
        "url": expected.url,
        "base": expected.base,
        "base_oid": expected.base_oid,
        "head": expected.head,
        "head_oid": expected.head_oid,
        "is_draft": is_draft,
        "transport_title_sha256": _sha_text(title),
        "transport_body_sha256": _sha_text(body),
        "target_title_sha256": _sha_text(entry["title"]),
        "target_body_sha256": _sha_text(target_body),
        "target_is_draft": target_is_draft,
        "target_review_input_sha256": entry["review_input_sha256"],
        "target_review_mode": review_mode,
        "target_publication_candidate_sha256": target_candidate_sha256,
        "target_review_bundle": str(review_bundle)
        if review_bundle is not None
        else None,
        "target_selected_specialists": specialists,
        "target_identity_epoch": {
            "repository": repository,
            "pr_number": expected.pr_number,
            "url": expected.url,
            "base": expected.base,
            "base_oid": expected.base_oid,
            "head": expected.head,
            "head_oid": expected.head_oid,
            "head_owner": expected.head_owner,
            "head_repository": expected.head_repository,
        },
        "publisher_commands": commands,
        "final_audit_command": audit,
    }


def execute(plan: dict[str, Any], output_path: Path) -> dict[str, Any]:
    receipt_root = prepare_receipt_store()
    root = Path(plan["request"]["repository_root"])
    lock_entries = sorted(
        plan["candidates"],
        key=lambda entry: (
            entry["base"],
            entry["head"],
            entry["head_owner"],
            entry["head_repository"],
        ),
    )
    with ExitStack() as stack:
        for entry in lock_entries:
            stack.enter_context(
                creation_transaction_lock(
                    receipt_root,
                    repository=plan["request"]["repository"],
                    base=entry["base"],
                    head=entry["head"],
                    head_owner=entry["head_owner"],
                    head_repository=entry["head_repository"],
                )
            )
        rebuilt = build_plan(plan["request"])
        if rebuilt != plan:
            raise GraphiteTransportError(
                "live state or candidate inputs drifted from plan"
            )
        command_error: str | None = None
        try:
            _run(
                [
                    "gt",
                    "submit",
                    "--no-stack",
                    "--draft",
                    "--no-edit",
                    "--no-ai",
                    "--no-interactive",
                ],
                cwd=root,
                timeout=MUTATION_TIMEOUT_SECONDS,
            )
        except GraphiteTransportError as error:
            command_error = str(error)
        handoffs: list[dict[str, Any]] = []
        failures: list[str] = []
        for index, entry in enumerate(plan["candidates"]):
            try:
                matches = _matching_prs(entry, plan["request"]["repository"])
                if len(matches) != 1:
                    raise GraphiteTransportError(
                        "transport did not yield exactly one open PR for the head/base"
                    )
                handoffs.append(
                    _handoff_entry(
                        entry,
                        plan["preimages"][index],
                        matches[0],
                        plan["request"]["repository"],
                    )
                )
            except GraphiteTransportError as error:
                failures.append(f"stack[{index}]: {error}")
    status = (
        "transport-complete-repair-required"
        if not failures
        else "transport-ambiguous-inspection-required"
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "plan_sha256": plan["content_sha256"],
        "repository_root": str(root.resolve()),
        "transport_command_error": command_error,
        "pull_requests": handoffs,
        "failures": failures,
    }
    unsigned = dict(result)
    result["content_sha256"] = _sha_bytes(_canonical(unsigned))
    _write_private_json(output_path, result)
    if failures:
        raise GraphiteTransportError(
            "Graphite transport is ambiguous; inspect the private handoff output"
        )
    return result


def _load_handoff(path: Path) -> dict[str, Any]:
    handoff = _strict_json(path)
    _exact_keys(
        handoff,
        {
            "schema_version",
            "status",
            "plan_sha256",
            "repository_root",
            "transport_command_error",
            "pull_requests",
            "failures",
            "content_sha256",
        },
        "handoff",
    )
    supplied = handoff["content_sha256"]
    if handoff["schema_version"] != SCHEMA_VERSION:
        raise GraphiteTransportError(f"handoff schema_version must be {SCHEMA_VERSION}")
    unsigned = dict(handoff)
    del unsigned["content_sha256"]
    if not isinstance(supplied, str) or supplied != _sha_bytes(_canonical(unsigned)):
        raise GraphiteTransportError("handoff content digest does not match")
    if handoff["status"] != "transport-complete-repair-required":
        raise GraphiteTransportError(
            "only a complete transport handoff can be repaired"
        )
    if handoff["failures"] != [] or not isinstance(handoff["pull_requests"], list):
        raise GraphiteTransportError("transport handoff is incomplete")
    return handoff


def _checked_helper_command(command: Any, *, operation: str) -> list[str]:
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(item, str) or not item for item in command)
    ):
        raise GraphiteTransportError("publisher handoff command is invalid")
    executable = Path(command[0]).resolve()
    if executable != Path(sys.executable).resolve():
        raise GraphiteTransportError("publisher handoff executable drifted")
    expected_script = UPDATE if operation == "publish" else AUDIT
    if len(command) < 3 or Path(command[1]).resolve() != expected_script.resolve():
        raise GraphiteTransportError("publisher handoff script drifted")
    allowed_operations = {"text", "ready"} if operation == "publish" else {"audit"}
    if command[2] not in allowed_operations:
        raise GraphiteTransportError("publisher handoff operation is invalid")
    return command


def _exact_flag_value(command: list[str], flag: str) -> str | None:
    positions = [index for index, value in enumerate(command) if value == flag]
    if not positions:
        return None
    if len(positions) != 1 or positions[0] + 1 >= len(command):
        raise GraphiteTransportError(f"publisher handoff {flag} is ambiguous")
    return command[positions[0] + 1]


def _checked_publisher_command(command: Any, item: dict[str, Any]) -> list[str]:
    checked = _checked_helper_command(command, operation="publish")
    mode = item.get("target_review_mode")
    bundle = item.get("target_review_bundle")
    specialists = item.get("target_selected_specialists")
    if _exact_flag_value(checked, "--review-mode") != mode:
        raise GraphiteTransportError("publisher handoff review mode drifted")
    selected = _exact_flag_value(checked, "--selected-specialists")
    if not isinstance(specialists, list) or selected != _canonical(specialists).decode(
        "utf-8"
    ):
        raise GraphiteTransportError("publisher handoff specialists drifted")
    command_bundle = _exact_flag_value(checked, "--review-bundle")
    if mode == "required":
        if not isinstance(bundle, str) or not Path(bundle).is_absolute():
            raise GraphiteTransportError("required review bundle target is invalid")
        if command_bundle != bundle:
            raise GraphiteTransportError("publisher handoff review bundle drifted")
    elif mode == "not-required":
        if bundle is not None or command_bundle is not None:
            raise GraphiteTransportError(
                "not-required publisher handoff carries a review bundle"
            )
    else:
        raise GraphiteTransportError("publisher handoff review mode is invalid")
    return checked


def _run_json_command(
    command: list[str], *, cwd: Path, operation: str
) -> dict[str, Any]:
    checked = _checked_helper_command(command, operation=operation)
    result = _run(checked, cwd=cwd, timeout=MUTATION_TIMEOUT_SECONDS)
    return _parse_strict_json(result.stdout, "publisher helper output")


def _repair_checkpoint_path(output_path: Path) -> Path:
    return output_path.with_name(f".{output_path.name}.checkpoint.json")


def _repair_checkpoint(
    output_path: Path, handoff: dict[str, Any]
) -> tuple[Path, dict[str, Any]]:
    path = _repair_checkpoint_path(output_path)
    if not path.exists():
        return path, {
            "schema_version": SCHEMA_VERSION,
            "handoff_sha256": handoff["content_sha256"],
            "completed": [],
        }
    checkpoint = _strict_json(path)
    _exact_keys(
        checkpoint,
        {"schema_version", "handoff_sha256", "completed"},
        "repair checkpoint",
    )
    if (
        checkpoint["schema_version"] != SCHEMA_VERSION
        or checkpoint["handoff_sha256"] != handoff["content_sha256"]
        or not isinstance(checkpoint["completed"], list)
    ):
        raise GraphiteTransportError("repair checkpoint does not match handoff")
    return path, checkpoint


def _audit_summary(audit: dict[str, Any]) -> dict[str, Any]:
    return {
        key: audit[key]
        for key in (
            "status",
            "receipt_id",
            "provenance",
            "sequence",
            "identity_epoch",
            "final",
            "review_input_sha256",
            "review",
        )
        if key in audit
    }


def _review_matches_target(audit: dict[str, Any], item: dict[str, Any]) -> bool:
    review = audit.get("review")
    if not isinstance(review, dict) or set(review) != {
        "mode",
        "publication_candidate_sha256",
        "observation",
    }:
        return False
    mode = item.get("target_review_mode")
    if (
        mode not in {"required", "not-required"}
        or review.get("mode") != mode
        or review.get("publication_candidate_sha256")
        != item.get("target_publication_candidate_sha256")
    ):
        return False
    observation = review.get("observation")
    return isinstance(observation, dict) if mode == "required" else observation is None


def _validate_handoff_target(item: dict[str, Any]) -> None:
    candidate_sha = item.get("target_publication_candidate_sha256")
    mode = item.get("target_review_mode")
    specialists = item.get("target_selected_specialists")
    bundle = item.get("target_review_bundle")
    if (
        not isinstance(candidate_sha, str)
        or len(candidate_sha) != 64
        or any(character not in "0123456789abcdef" for character in candidate_sha)
    ):
        raise GraphiteTransportError("handoff publication candidate digest is invalid")
    if (
        not isinstance(specialists, list)
        or not all(isinstance(value, str) and value for value in specialists)
        or specialists != sorted(set(specialists))
    ):
        raise GraphiteTransportError("handoff review specialists are invalid")
    if mode == "required":
        if not isinstance(bundle, str) or not Path(bundle).is_absolute():
            raise GraphiteTransportError("handoff required review bundle is invalid")
    elif mode == "not-required":
        if bundle is not None:
            raise GraphiteTransportError(
                "handoff not-required review bundle must be null"
            )
    else:
        raise GraphiteTransportError("handoff review mode is invalid")


def _audit_matches_target(audit: dict[str, Any], item: dict[str, Any]) -> bool:
    return (
        set(audit)
        == {
            "status",
            "receipt_id",
            "provenance",
            "sequence",
            "identity_epoch",
            "final",
            "review_input_sha256",
            "review",
        }
        and audit.get("status") == "verified"
        and isinstance(audit.get("receipt_id"), str)
        and bool(audit["receipt_id"])
        and audit.get("provenance") == "canonical"
        and type(audit.get("sequence")) is int
        and audit["sequence"] > 0
        and audit.get("identity_epoch") == item.get("target_identity_epoch")
        and audit.get("final")
        == {
            "is_draft": item.get("target_is_draft"),
            "title_sha256": item.get("target_title_sha256"),
            "body_sha256": item.get("target_body_sha256"),
        }
        and audit.get("review_input_sha256") == item.get("target_review_input_sha256")
        and _review_matches_target(audit, item)
    )


def repair(handoff: dict[str, Any], output_path: Path) -> dict[str, Any]:
    root = Path(handoff["repository_root"])
    if not root.is_absolute() or not root.is_dir():
        raise GraphiteTransportError("handoff repository root is unavailable")
    checkpoint_path, checkpoint = _repair_checkpoint(output_path, handoff)
    completed = {
        item.get("pr"): item
        for item in checkpoint["completed"]
        if isinstance(item, dict) and type(item.get("pr")) is int
    }
    if len(completed) != len(checkpoint["completed"]):
        raise GraphiteTransportError("repair checkpoint PR inventory is invalid")
    repaired: list[dict[str, Any]] = []
    for item in handoff["pull_requests"]:
        if not isinstance(item, dict):
            raise GraphiteTransportError("handoff PR entry is invalid")
        _validate_handoff_target(item)
        commands = item.get("publisher_commands")
        if not isinstance(commands, list):
            raise GraphiteTransportError("handoff publisher commands are invalid")
        checked_commands = [
            _checked_publisher_command(command, item) for command in commands
        ]
        audit_command = _checked_helper_command(
            item.get("final_audit_command"), operation="audit"
        )
        try:
            current_audit = _run_json_command(
                audit_command, cwd=root, operation="audit"
            )
        except GraphiteTransportError:
            current_audit = {"status": "unavailable"}
        prior = completed.get(item.get("pr"))
        if prior is not None:
            if (
                not _audit_matches_target(current_audit, item)
                or _audit_summary(current_audit) != prior.get("audit")
                or prior.get("target_title_sha256") != item.get("target_title_sha256")
                or prior.get("target_body_sha256") != item.get("target_body_sha256")
                or prior.get("target_review_mode") != item.get("target_review_mode")
                or prior.get("target_publication_candidate_sha256")
                != item.get("target_publication_candidate_sha256")
                or prior.get("target_review_bundle") != item.get("target_review_bundle")
                or prior.get("target_selected_specialists")
                != item.get("target_selected_specialists")
            ):
                raise GraphiteTransportError(
                    "checkpointed PR no longer has its exact verified target"
                )
            repaired.append(prior)
            continue
        command_results: list[dict[str, Any]] = []
        if _audit_matches_target(current_audit, item):
            audit = current_audit
        elif commands:
            command_results = [
                _run_json_command(command, cwd=root, operation="publish")
                for command in checked_commands
            ]
            audit = _run_json_command(audit_command, cwd=root, operation="audit")
        else:
            audit = current_audit
        if not _audit_matches_target(audit, item):
            raise GraphiteTransportError(
                "publisher audit did not verify the exact handoff target"
            )
        record = {
            "repository": item.get("repository"),
            "pr": item.get("pr"),
            "url": item.get("url"),
            "target_title_sha256": item.get("target_title_sha256"),
            "target_body_sha256": item.get("target_body_sha256"),
            "target_is_draft": item.get("target_is_draft"),
            "target_review_input_sha256": item.get("target_review_input_sha256"),
            "target_review_mode": item.get("target_review_mode"),
            "target_publication_candidate_sha256": item.get(
                "target_publication_candidate_sha256"
            ),
            "target_review_bundle": item.get("target_review_bundle"),
            "target_selected_specialists": item.get("target_selected_specialists"),
            "target_identity_epoch": item.get("target_identity_epoch"),
            "publisher_result_sha256": [
                _sha_bytes(_canonical(result)) for result in command_results
            ],
            "audit": _audit_summary(audit),
        }
        repaired.append(record)
        checkpoint["completed"].append(record)
        _write_private_json(checkpoint_path, checkpoint)
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "canonical-repair-complete",
        "handoff_sha256": handoff["content_sha256"],
        "pull_requests": repaired,
    }
    unsigned = dict(result)
    result["content_sha256"] = _sha_bytes(_canonical(unsigned))
    _write_private_json(output_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--request", required=True, type=Path)
    plan_parser.add_argument("--output", required=True, type=Path)
    execute_parser = subparsers.add_parser("execute")
    execute_parser.add_argument("--plan", required=True, type=Path)
    execute_parser.add_argument("--output", required=True, type=Path)
    repair_parser = subparsers.add_parser("repair")
    repair_parser.add_argument("--handoff", required=True, type=Path)
    repair_parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.operation == "plan":
            plan = build_plan(_strict_json(args.request))
            _write_private_json(args.output, plan)
            result = {
                "status": "ready",
                "plan_sha256": plan["content_sha256"],
                "output": str(args.output),
            }
        elif args.operation == "execute":
            result = execute(_load_plan(args.plan), args.output)
        else:
            result = repair(_load_handoff(args.handoff), args.output)
    except GraphiteTransportError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
