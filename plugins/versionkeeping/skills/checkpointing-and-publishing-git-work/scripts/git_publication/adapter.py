"""Git adapter for deterministic publication planning.

Remote graph observation may fetch bounded objects and create target-local
temporary refs. The refs are removed with exact-value checks; fetched objects
may persist. Callers must not use this planner for read-only Git tasks.
"""

import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
import urllib.parse
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, IO, Iterator, List, Optional, Sequence, Tuple

from .core import (
    AbsentTarget,
    CreationBase,
    PresentTarget,
    PublicationRequest,
    RepositorySnapshot,
    planner_effects,
    plan_publication,
    request_document,
)


SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
OBJECT_FORMAT_WIDTHS = {"sha1": 40, "sha256": 64}
EXPECTED_KEYS = {
    "schema_version",
    "start_head",
    "source_sha",
    "task_owned_commits",
    "adopted_commits",
    "removal_authorized_commits",
    "explicit_destination",
    "default_branch_policy",
    "allow_create",
    "creation_base_ref",
}
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
SCP_ENDPOINT_RE = re.compile(
    r"(?:(?P<user>[^/@:\s]+)@)?"
    r"(?P<host>\[[0-9A-Fa-f:.]+\]|[^/:\s]+):"
    r"(?P<path>[^:\s][^\s]*)"
)
DEFAULT_GIT_TIMEOUT_SECONDS = 30
SCRUBBED_GIT_ENV_NAMES = {
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_ASKPASS",
    "GIT_ATTR_NOSYSTEM",
    "GIT_CEILING_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_CONFIG",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_NOSYSTEM",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_SYSTEM",
    "GIT_DIR",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_EDITOR",
    "GIT_EXEC_PATH",
    "GIT_EXTERNAL_DIFF",
    "GIT_FLUSH",
    "GIT_GLOB_PATHSPECS",
    "GIT_ICASE_PATHSPECS",
    "GIT_INDEX_FILE",
    "GIT_INTERNAL_SUPER_PREFIX",
    "GIT_LITERAL_PATHSPECS",
    "GIT_NAMESPACE",
    "GIT_NOGLOB_PATHSPECS",
    "GIT_OBJECT_DIRECTORY",
    "GIT_OPTIONAL_LOCKS",
    "GIT_PREFIX",
    "GIT_PROTOCOL",
    "GIT_PROTOCOL_FROM_USER",
    "GIT_QUARANTINE_PATH",
    "GIT_REPLACE_REF_BASE",
    "GIT_SEQUENCE_EDITOR",
    "GIT_SHALLOW_FILE",
    "GIT_SSL_CAINFO",
    "GIT_SSL_CAPATH",
    "GIT_SSL_NO_VERIFY",
    "GIT_SSH",
    "GIT_SSH_COMMAND",
    "GIT_SSH_VARIANT",
    "GIT_SUPER_PREFIX",
    "GIT_WORK_TREE",
    "SSH_ASKPASS",
    "VERSIONKEEPING_PUBLICATION_ENDPOINT",
}


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
    if re.fullmatch(r"protocol(?:\.[^.]+)?\.allow", normalized):
        return "protocol.*.allow"
    if re.fullmatch(r"filter\..+\.(?:clean|smudge|process)", normalized):
        return "filter.*.(clean|smudge|process)"
    if re.fullmatch(r"hook\..+\.command", normalized):
        return "hook.*.command"
    if re.fullmatch(r"gpg\..+\.program", normalized):
        return "gpg.*.program"
    if re.fullmatch(r"credential(?:\..+)?\.helper", normalized):
        return "credential.*.helper"
    if re.fullmatch(r"diff\..+\.(?:command|textconv)", normalized):
        return "diff.*.(command|textconv)"
    if re.fullmatch(r"difftool\..+\.cmd", normalized):
        return "difftool.*.cmd"
    if re.fullmatch(r"merge\..+\.driver", normalized):
        return "merge.*.driver"
    if re.fullmatch(r"mergetool\..+\.cmd", normalized):
        return "mergetool.*.cmd"
    if re.fullmatch(r"remote\..+\.(?:vcs|uploadpack|receivepack)", normalized):
        return "remote.*.(vcs|uploadpack|receivepack)"
    if re.fullmatch(r"url\..+\.(?:insteadof|pushinsteadof)", normalized):
        return "url.*.(insteadOf|pushInsteadOf)"
    if re.fullmatch(r"submodule\..+\.update", normalized):
        return "submodule.*.update"
    return None


class MalformedRequest(ValueError):
    """The request does not conform to schema version 2."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise MalformedRequest(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str):
    raise MalformedRequest(f"non-finite JSON value: {value}")


def load_request_json(stream: IO[str]) -> Any:
    """Decode strict planner request JSON."""
    return json.load(
        stream,
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )


class PolicyGate(RuntimeError):
    def __init__(self, code: str, **evidence: Any):
        super().__init__(code)
        self.code = code
        self.evidence = evidence
        self.context = {}

    def retain_context(self, context: dict) -> None:
        self.context = dict(context)


class GitObjectFormat:
    def __init__(self, name: str, oid_width: int):
        self.name = name
        self.oid_width = oid_width

    def matches(self, value: object) -> bool:
        return (
            isinstance(value, str)
            and len(value) == self.oid_width
            and re.fullmatch(r"[0-9a-f]+", value) is not None
        )


def _validate_sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise MalformedRequest(f"{field} must be a lowercase full Git object ID")
    return value


def _object_format(repo: "GitRepository") -> GitObjectFormat:
    value = repo.output(["rev-parse", "--show-object-format=storage"])
    width = OBJECT_FORMAT_WIDTHS.get(value)
    if width is None:
        raise PolicyGate("UNSUPPORTED_OR_MIXED_GIT_OBJECT_FORMAT", value=value)
    return GitObjectFormat(value, width)


def _bind_request_object_format(
    request: PublicationRequest, object_format: GitObjectFormat
) -> None:
    values = {
        "start_head": (request.start_head,),
        "source_sha": (request.source_sha,),
        "task_owned_commits": request.task_owned_commits,
        "adopted_commits": request.adopted_commits,
        "removal_authorized_commits": request.removal_authorized_commits,
    }
    for field, object_ids in values.items():
        if not all(object_format.matches(object_id) for object_id in object_ids):
            raise PolicyGate(
                "GIT_OBJECT_ID_FORMAT_MISMATCH",
                field=field,
                object_format=object_format.name,
            )


def _validate_remote(value: Any) -> str:
    if not isinstance(value, str) or not value or CONTROL_RE.search(value):
        raise MalformedRequest(
            "destination remote must be a nonempty control-free string"
        )
    return value


def _validate_heads_ref(value: Any, field: str = "destination ref") -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("refs/heads/")
        or value == "refs/heads/"
        or value.startswith("-")
        or CONTROL_RE.search(value)
        or any(
            token in value
            for token in ("..", "@{", "\\", " ", "~", "^", ":", "?", "*", "[")
        )
        or value.endswith("/")
        or value.endswith(".")
        or "//" in value
        or any(
            component.startswith(".") or component.endswith(".lock")
            for component in value.split("/")
        )
    ):
        raise MalformedRequest(f"{field} must be one full refs/heads/... ref")
    return value


def _sha_set(value: Any, field: str) -> frozenset:
    if not isinstance(value, list):
        raise MalformedRequest(f"{field} must be an array")
    checked = [_validate_sha(item, field) for item in value]
    if len(set(checked)) != len(checked):
        raise MalformedRequest(f"{field} must not contain duplicates")
    return frozenset(checked)


def _default_branch_policy(value: Any) -> Optional[dict]:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "ref",
        "direct_push_permitted",
    }:
        raise MalformedRequest(
            "default_branch_policy must be null or contain exactly ref and "
            "direct_push_permitted"
        )
    if type(value["direct_push_permitted"]) is not bool:
        raise MalformedRequest(
            "default_branch_policy direct_push_permitted must be boolean"
        )
    return {
        "ref": _validate_heads_ref(value["ref"], "default_branch_policy ref"),
        "direct_push_permitted": value["direct_push_permitted"],
    }


def parse_request(raw: Any) -> PublicationRequest:
    if not isinstance(raw, dict):
        raise MalformedRequest("request must be a JSON object")
    if set(raw) != EXPECTED_KEYS:
        missing = sorted(EXPECTED_KEYS - set(raw))
        extra = sorted(set(raw) - EXPECTED_KEYS)
        raise MalformedRequest(
            f"request fields differ from schema; missing={missing}, extra={extra}"
        )
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 2:
        raise MalformedRequest("schema_version must equal 2")
    if type(raw["allow_create"]) is not bool:
        raise MalformedRequest("allow_create must be boolean")

    explicit = raw["explicit_destination"]
    if explicit is not None:
        if not isinstance(explicit, dict) or set(explicit) != {"remote", "ref"}:
            raise MalformedRequest(
                "explicit_destination must contain exactly remote and ref"
            )
        explicit = {
            "remote": _validate_remote(explicit["remote"]),
            "ref": _validate_heads_ref(explicit["ref"]),
        }
    creation_base = raw["creation_base_ref"]
    if creation_base is not None:
        creation_base = _validate_heads_ref(creation_base, "creation_base_ref")

    return PublicationRequest(
        schema_version=2,
        start_head=_validate_sha(raw["start_head"], "start_head"),
        source_sha=_validate_sha(raw["source_sha"], "source_sha"),
        task_owned_commits=_sha_set(raw["task_owned_commits"], "task_owned_commits"),
        adopted_commits=_sha_set(raw["adopted_commits"], "adopted_commits"),
        removal_authorized_commits=_sha_set(
            raw["removal_authorized_commits"], "removal_authorized_commits"
        ),
        explicit_destination=explicit,
        default_branch_policy=_default_branch_policy(raw["default_branch_policy"]),
        allow_create=raw["allow_create"],
        creation_base_ref=creation_base,
    )


class GitRepository:
    def __init__(self, path: Path, timeout_seconds: int = DEFAULT_GIT_TIMEOUT_SECONDS):
        self.path = Path(path)
        self.timeout_seconds = timeout_seconds
        self._empty_hooks = tempfile.TemporaryDirectory(
            prefix="versionkeeping-empty-hooks-"
        )
        self.hooks_path = Path(self._empty_hooks.name).resolve()
        self._policy_established = False
        self.env = {
            key: value
            for key, value in os.environ.items()
            if key not in SCRUBBED_GIT_ENV_NAMES
            and not key.startswith("GIT_CONFIG_KEY_")
            and not key.startswith("GIT_CONFIG_VALUE_")
            and not key.startswith("GIT_TRACE")
            and key != "GIT_CONFIG_COUNT"
        }
        self.env.update(
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
                "GIT_SEQUENCE_EDITOR": "true",
                "GIT_SSH_COMMAND": (
                    "ssh -oBatchMode=yes -oConnectionAttempts=1 "
                    f"-oConnectTimeout={timeout_seconds}"
                ),
                "GIT_TERMINAL_PROMPT": "0",
                "GCM_INTERACTIVE": "never",
                "LC_ALL": "C",
                "SSH_ASKPASS_REQUIRE": "never",
            }
        )
        closed_config = (
            ("core.hooksPath", str(self.hooks_path)),
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
        self.env["GIT_CONFIG_COUNT"] = str(len(closed_config))
        for index, (key, value) in enumerate(closed_config):
            self.env[f"GIT_CONFIG_KEY_{index}"] = key
            self.env[f"GIT_CONFIG_VALUE_{index}"] = value

    def __enter__(self) -> "GitRepository":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._empty_hooks.cleanup()

    def _run_raw(
        self,
        args: Sequence[str],
        check: bool = True,
        allowed: Sequence[int] = (),
        env_overrides: Optional[Dict[str, str]] = None,
    ) -> subprocess.CompletedProcess:
        env = self.env.copy()
        if env_overrides:
            if any(
                key == "GIT_CONFIG_COUNT"
                or key.startswith("GIT_CONFIG_KEY_")
                or key.startswith("GIT_CONFIG_VALUE_")
                for key in env_overrides
            ):
                raise PolicyGate("GIT_CONFIG_ENVIRONMENT_OVERRIDE_REJECTED")
            env.update(env_overrides)
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=str(self.path),
                env=env,
                stdin=subprocess.DEVNULL,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise PolicyGate(
                "GIT_COMMAND_TIMEOUT",
                operation=args[0] if args else "git",
                timeout_seconds=self.timeout_seconds,
            ) from error
        if check and completed.returncode != 0 and completed.returncode not in allowed:
            raise RuntimeError(
                f"git command failed ({completed.returncode}): "
                f"{args[0] if args else 'git'}"
            )
        return completed

    def _config_key_inventory(
        self,
        *,
        includes: bool,
    ) -> list[tuple[str, str, str]]:
        result = self._run_raw(
            [
                "config",
                "--null",
                "--show-origin",
                "--show-scope",
                "--includes" if includes else "--no-includes",
                "--name-only",
                "--list",
            ],
            check=False,
        )
        if result.returncode != 0:
            raise PolicyGate("GIT_CONFIGURATION_UNAVAILABLE")
        fields = result.stdout.split("\0")
        if fields and fields[-1] == "":
            fields.pop()
        if len(fields) % 3 != 0:
            raise PolicyGate("GIT_CONFIGURATION_INVENTORY_MALFORMED")
        return [
            (fields[index], fields[index + 1], fields[index + 2])
            for index in range(0, len(fields), 3)
        ]

    def _establish_inert_policy(self) -> None:
        if self._policy_established:
            return
        without_includes = self._config_key_inventory(includes=False)
        include_classes = {
            config_class
            for scope, _origin, key in without_includes
            if scope != "command"
            if (config_class := _unsafe_git_config_class(key))
            == "include*.path"
        }
        if include_classes:
            raise PolicyGate(
                "UNSAFE_GIT_CONFIGURATION",
                config_classes=sorted(include_classes),
            )
        inventory = self._config_key_inventory(includes=True)
        unsafe_classes = {
            config_class
            for scope, _origin, key in inventory
            if scope != "command"
            if (config_class := _unsafe_git_config_class(key)) is not None
        }
        if unsafe_classes:
            raise PolicyGate(
                "UNSAFE_GIT_CONFIGURATION",
                config_classes=sorted(unsafe_classes),
            )
        self._policy_established = True

    def run(
        self,
        args: Sequence[str],
        check: bool = True,
        allowed: Sequence[int] = (),
        env_overrides: Optional[Dict[str, str]] = None,
    ) -> subprocess.CompletedProcess:
        self._establish_inert_policy()
        return self._run_raw(
            args,
            check=check,
            allowed=allowed,
            env_overrides=env_overrides,
        )

    def output(self, args: Sequence[str], allowed: Sequence[int] = ()) -> str:
        return self.run(args, allowed=allowed).stdout.strip()

    def config_all(self, key: str) -> List[str]:
        result = self.run(["config", "--get-all", "--", key], check=False)
        if result.returncode == 1:
            return []
        if result.returncode != 0:
            raise RuntimeError(f"unable to read Git config key {key}")
        return result.stdout.splitlines()

    def git_path(self, name: str) -> Path:
        value = Path(self.output(["rev-parse", "--git-path", name]))
        return value if value.is_absolute() else self.path / value


def _blocked(request: PublicationRequest, gate: PolicyGate) -> dict:
    destination = gate.context.get("destination")
    target = gate.context.get("target", {"present": None, "sha": None})
    return {
        "schema_version": 1,
        "status": "blocked",
        "request": request_document(request),
        "planner_effects": planner_effects(),
        "reasons": [{"code": gate.code, "evidence": gate.evidence}],
        "source_sha": request.source_sha,
        "destination": destination,
        "target": target,
        "outgoing_shas": [],
        "target_only_shas": [],
        "fast_forward": False,
        "rewrite_required": False,
        "push": None,
        "postchecks": [],
    }


def _partial_clone_keys(partial_config: str) -> list[str]:
    partial = []
    for line in partial_config.splitlines():
        key, _, value = line.partition(" ")
        if key.lower() == "extensions.partialclone" and value:
            partial.append(key)
        elif key.endswith(".promisor") and value.lower() in ("true", "yes", "on", "1"):
            partial.append(key)
        elif key.endswith(".partialclonefilter") and value:
            partial.append(key)
    return partial


def _active_operation_markers(repo: GitRepository) -> list[str]:
    operation_paths = (
        "MERGE_HEAD",
        "rebase-merge",
        "rebase-apply",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "BISECT_LOG",
        "sequencer",
    )
    return [name for name in operation_paths if repo.git_path(name).exists()]


def _guard_repository(repo: GitRepository) -> None:
    inside = repo.run(["rev-parse", "--is-inside-work-tree"], check=False)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise PolicyGate("NOT_A_WORKTREE")
    if repo.output(["rev-parse", "--is-shallow-repository"]) == "true":
        raise PolicyGate("SHALLOW_REPOSITORY")

    partial_config = repo.output(
        [
            "config",
            "--get-regexp",
            r"^(extensions\.partialclone|remote\..*\.(promisor|partialclonefilter))$",
        ],
        allowed=(1,),
    )
    partial = _partial_clone_keys(partial_config)
    if partial:
        raise PolicyGate("PARTIAL_OR_PROMISOR_REPOSITORY", config_keys=partial)

    replace_refs = repo.output(["for-each-ref", "--format=%(refname)", "refs/replace/"])
    if replace_refs:
        raise PolicyGate("REPLACE_REFS_PRESENT", refs=replace_refs.splitlines())

    grafts = repo.git_path("info/grafts")
    if grafts.exists() and grafts.read_bytes().strip():
        raise PolicyGate("LEGACY_GRAFTS_PRESENT")

    active = _active_operation_markers(repo)
    if active:
        raise PolicyGate("GIT_OPERATION_IN_PROGRESS", markers=active)


def _ensure_commits(repo: GitRepository, request: PublicationRequest) -> None:
    for field, sha in (
        ("start_head", request.start_head),
        ("source_sha", request.source_sha),
    ):
        result = repo.run(["cat-file", "-e", f"{sha}^{{commit}}"], check=False)
        if result.returncode != 0:
            raise PolicyGate("REQUESTED_COMMIT_UNAVAILABLE", field=field, sha=sha)


def _upstream(
    repo: GitRepository, branch_ref: str
) -> Tuple[Optional[str], Optional[str]]:
    value = repo.output(
        [
            "for-each-ref",
            "--format=%(upstream:remotename)%00%(upstream:remoteref)",
            "--",
            branch_ref,
        ]
    )
    if not value or "\x00" not in value:
        return None, None
    remote, ref = value.split("\x00", 1)
    return (remote or None), (ref or None)


def _default_push_ref(
    mode: str,
    branch_ref: str,
    selected_remote: str,
    upstream_remote: Optional[str],
    upstream_ref: Optional[str],
) -> str:
    if mode == "nothing":
        raise PolicyGate("PUSH_DEFAULT_NOTHING")
    if mode == "matching":
        raise PolicyGate("PUSH_DEFAULT_AMBIGUOUS", mode=mode)
    if mode == "current":
        return branch_ref
    if mode == "upstream":
        if not upstream_ref or upstream_remote != selected_remote:
            raise PolicyGate("PUSH_DEFAULT_AMBIGUOUS", mode=mode)
        return upstream_ref
    if mode == "simple":
        if upstream_ref is None:
            raise PolicyGate(
                "PUSH_DEFAULT_AMBIGUOUS", mode=mode, reason="upstream_missing"
            )
        if upstream_remote != selected_remote or upstream_ref != branch_ref:
            raise PolicyGate("PUSH_DEFAULT_AMBIGUOUS", mode=mode)
        return branch_ref
    raise PolicyGate("PUSH_DEFAULT_UNSUPPORTED", mode=mode)


def _remote_push_ref(values: List[str], branch_ref: str) -> Optional[str]:
    if not values:
        return None
    if len(values) != 1 or any("*" in value for value in values):
        raise PolicyGate("REMOTE_PUSH_AMBIGUOUS", count=len(values))
    value = values[0]
    if value.startswith("+"):
        value = value[1:]
    if ":" in value:
        source, target = value.split(":", 1)
    else:
        source, target = value, value
    aliases = {branch_ref, branch_ref[len("refs/heads/") :], "HEAD"}
    if source not in aliases:
        raise PolicyGate("REMOTE_PUSH_DOES_NOT_SELECT_CURRENT_BRANCH")
    if source in {branch_ref[len("refs/heads/") :], "HEAD"} and ":" not in value:
        target = branch_ref
    try:
        return _validate_heads_ref(target, "remote.push target")
    except MalformedRequest:
        raise PolicyGate("REMOTE_PUSH_INVALID_TARGET")


def _select_remote(
    repo: GitRepository,
    remotes: list[str],
    branch: str,
    upstream_remote: Optional[str],
) -> tuple[str, str]:
    push_remote = repo.config_all(f"branch.{branch}.pushRemote")
    push_default_remote = repo.config_all("remote.pushDefault")
    if len(push_remote) > 1 or len(push_default_remote) > 1:
        raise PolicyGate("DESTINATION_REMOTE_AMBIGUOUS")
    if push_remote:
        remote, selection = push_remote[0], "branch.pushRemote"
    elif push_default_remote:
        remote, selection = push_default_remote[0], "remote.pushDefault"
    elif upstream_remote:
        remote, selection = upstream_remote, "upstream"
    elif len(remotes) == 1:
        remote, selection = remotes[0], "sole_remote"
    else:
        raise PolicyGate("DESTINATION_REMOTE_AMBIGUOUS", remote_count=len(remotes))
    try:
        _validate_remote(remote)
    except MalformedRequest as error:
        raise PolicyGate("DESTINATION_REMOTE_INVALID") from error
    if remote not in remotes:
        raise PolicyGate("DESTINATION_REMOTE_NOT_CONFIGURED", remote=remote)
    return remote, selection


def _select_push_ref(
    repo: GitRepository,
    remote: str,
    branch_ref: str,
    upstream_remote: Optional[str],
    upstream_ref: Optional[str],
) -> tuple[str, str, list[str]]:
    push_default_values = repo.config_all("push.default")
    if len(push_default_values) > 1:
        raise PolicyGate("PUSH_DEFAULT_AMBIGUOUS", count=len(push_default_values))
    mode = push_default_values[0] if push_default_values else "simple"
    default_ref = _default_push_ref(
        mode, branch_ref, remote, upstream_remote, upstream_ref
    )
    remote_push_values = repo.config_all(f"remote.{remote}.push")
    configured_ref = _remote_push_ref(remote_push_values, branch_ref)
    if configured_ref is not None and configured_ref != default_ref:
        raise PolicyGate(
            "PUSH_TARGET_CONFLICT",
            remote_push_ref=configured_ref,
            push_default_ref=default_ref,
        )
    return configured_ref or default_ref, mode, remote_push_values


def _resolve_destination(
    repo: GitRepository, request: PublicationRequest
) -> Tuple[str, str, dict]:
    remotes = repo.output(["remote"]).splitlines()
    if request.explicit_destination is not None:
        remote = request.explicit_destination["remote"]
        if remote not in remotes:
            raise PolicyGate("DESTINATION_REMOTE_NOT_CONFIGURED", remote=remote)
        return remote, request.explicit_destination["ref"], {"selection": "explicit"}

    branch_ref = repo.output(["symbolic-ref", "-q", "HEAD"], allowed=(1,))
    if not branch_ref.startswith("refs/heads/"):
        raise PolicyGate("DETACHED_HEAD_REQUIRES_EXPLICIT_DESTINATION")
    branch = branch_ref[len("refs/heads/") :]
    upstream_remote, upstream_ref = _upstream(repo, branch_ref)
    remote, selection = _select_remote(repo, remotes, branch, upstream_remote)
    ref, mode, remote_push_values = _select_push_ref(
        repo, remote, branch_ref, upstream_remote, upstream_ref
    )
    return (
        remote,
        ref,
        {
            "selection": selection,
            "branch_ref": branch_ref,
            "push_default": mode,
            "remote_push": remote_push_values,
        },
    )


def _fingerprint(endpoint: str) -> str:
    return "sha256:" + hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


def _require_secure_local_ancestry(path: Path) -> None:
    trusted_owners = {0, os.geteuid()}
    child = path.lstat()
    for parent in path.parents:
        metadata = parent.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in trusted_owners:
            raise PolicyGate(
                "UNSAFE_GIT_REMOTE",
                remote_kind="local",
                reason="ownership_or_mode",
            )
        writable = stat.S_IMODE(metadata.st_mode) & 0o022
        sticky_protection = bool(metadata.st_mode & stat.S_ISVTX) and (
            child.st_uid in trusted_owners
        )
        if writable and not sticky_protection:
            raise PolicyGate(
                "UNSAFE_GIT_REMOTE",
                remote_kind="local",
                reason="ownership_or_mode",
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
            raise PolicyGate(
                "UNSAFE_GIT_REMOTE",
                remote_kind="local",
                reason="ownership_or_mode",
            )


def _guard_local_remote(path: Path) -> None:
    try:
        lexical = Path(os.path.abspath(path))
        if lexical.is_symlink():
            raise OSError("symlinked local remote")
        _reject_untrusted_local_symlink_ancestors(lexical)
        resolved = lexical.resolve(strict=True)
        metadata = resolved.lstat()
    except (OSError, RuntimeError, ValueError) as error:
        raise PolicyGate(
            "UNSAFE_GIT_REMOTE",
            remote_kind="local",
            reason="unavailable",
        ) from error
    safe_kind = stat.S_ISDIR(metadata.st_mode) or (
        stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1
    )
    if (
        not safe_kind
        or resolved.is_symlink()
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise PolicyGate(
            "UNSAFE_GIT_REMOTE",
            remote_kind="local",
            reason="ownership_or_mode",
        )
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


def _validate_transport_endpoint(
    endpoint: str,
    repository_root: Path | None = None,
) -> None:
    if not endpoint or CONTROL_RE.search(endpoint):
        raise PolicyGate("UNSAFE_GIT_REMOTE", reason="malformed")
    lowered = endpoint.lower()
    if lowered.startswith("https://"):
        parsed = urllib.parse.urlsplit(endpoint)
        try:
            port = parsed.port
        except ValueError as error:
            raise PolicyGate("UNSAFE_GIT_REMOTE", remote_kind="https") from error
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.hostname.startswith("-")
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise PolicyGate("UNSAFE_GIT_REMOTE", remote_kind="https")
        return
    if lowered.startswith("ssh://"):
        parsed = urllib.parse.urlsplit(endpoint)
        try:
            port = parsed.port
        except ValueError as error:
            raise PolicyGate("UNSAFE_GIT_REMOTE", remote_kind="ssh") from error
        if (
            parsed.scheme.lower() != "ssh"
            or not parsed.hostname
            or parsed.hostname.startswith("-")
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise PolicyGate("UNSAFE_GIT_REMOTE", remote_kind="ssh")
        return
    if _is_scp_endpoint(endpoint):
        return
    if lowered.startswith("file://"):
        parsed = urllib.parse.urlsplit(endpoint)
        decoded = urllib.parse.unquote(parsed.path)
        if (
            parsed.netloc not in {"", "localhost"}
            or parsed.query
            or parsed.fragment
            or CONTROL_RE.search(decoded)
        ):
            raise PolicyGate("UNSAFE_GIT_REMOTE", remote_kind="local")
        local = Path(decoded)
    else:
        if urllib.parse.urlsplit(endpoint).scheme:
            raise PolicyGate("UNSAFE_GIT_REMOTE", reason="unsupported_protocol")
        local = Path(endpoint)
    if not local.is_absolute():
        if repository_root is None:
            raise PolicyGate("UNSAFE_GIT_REMOTE", reason="unsupported_protocol")
        local = repository_root / local
    _guard_local_remote(local)


def _endpoint(repo: GitRepository, remote: str) -> Tuple[str, str]:
    result = repo.run(
        ["remote", "get-url", "--push", "--all", "--", remote], check=False
    )
    if result.returncode != 0:
        raise PolicyGate("PUSH_ENDPOINT_UNAVAILABLE", remote=remote)
    urls = result.stdout.splitlines()
    if len(urls) != 1 or not urls[0]:
        raise PolicyGate("PUSH_ENDPOINT_AMBIGUOUS", count=len(urls))
    _validate_transport_endpoint(urls[0], repo.path)
    return urls[0], _fingerprint(urls[0])


def _run_endpoint(
    repo: GitRepository,
    endpoint: str,
    prefix: Sequence[str],
    suffix: Sequence[str] = (),
    *,
    check: bool = False,
) -> subprocess.CompletedProcess:
    alias = f"versionkeeping-publication-{secrets.token_hex(16)}"
    env_name = "VERSIONKEEPING_PUBLICATION_ENDPOINT"
    return repo.run(
        [
            "-c",
            "http.sslVerify=true",
            "-c",
            f"core.hooksPath={repo.hooks_path}",
            f"--config-env=remote.{alias}.url={env_name}",
            *prefix,
            "--",
            alias,
            *suffix,
        ],
        check=check,
        env_overrides={env_name: endpoint},
    )


def _probe_ref(
    repo: GitRepository,
    endpoint: str,
    ref: str,
    object_format: GitObjectFormat,
) -> Optional[str]:
    result = _run_endpoint(repo, endpoint, ["ls-remote", "--refs"], [ref])
    if result.returncode != 0:
        raise PolicyGate("PUSH_ENDPOINT_PROBE_FAILED")
    lines = result.stdout.splitlines()
    if not lines:
        return None
    if len(lines) != 1:
        raise PolicyGate("REMOTE_REF_PROBE_AMBIGUOUS", ref=ref)
    fields = lines[0].split("\t")
    if len(fields) != 2 or fields[1] != ref or not object_format.matches(fields[0]):
        raise PolicyGate("REMOTE_REF_PROBE_MALFORMED", ref=ref)
    return fields[0]


def _probe_default_branch(
    repo: GitRepository,
    endpoint: str,
    object_format: GitObjectFormat,
) -> str:
    result = _run_endpoint(repo, endpoint, ["ls-remote", "--symref"], ["HEAD"])
    if result.returncode != 0:
        raise PolicyGate("DEFAULT_BRANCH_OBSERVATION_FAILED")
    lines = result.stdout.splitlines()
    if len(lines) != 2:
        raise PolicyGate("DEFAULT_BRANCH_OBSERVATION_MALFORMED")
    symref_fields = lines[0].split("\t")
    target_fields = lines[1].split("\t")
    if (
        len(symref_fields) != 2
        or not symref_fields[0].startswith("ref: ")
        or symref_fields[1] != "HEAD"
        or len(target_fields) != 2
        or not object_format.matches(target_fields[0])
        or target_fields[1] != "HEAD"
    ):
        raise PolicyGate("DEFAULT_BRANCH_OBSERVATION_MALFORMED")
    try:
        return _validate_heads_ref(
            symref_fields[0][len("ref: ") :], "observed default branch"
        )
    except MalformedRequest as error:
        raise PolicyGate("DEFAULT_BRANCH_OBSERVATION_MALFORMED") from error


def _advertised_heads(
    repo: GitRepository, endpoint: str, object_format: GitObjectFormat
) -> Dict[str, str]:
    result = _run_endpoint(repo, endpoint, ["ls-remote", "--heads"])
    if result.returncode != 0:
        raise PolicyGate("PUSH_ENDPOINT_PROBE_FAILED")
    heads = {}
    for line in result.stdout.splitlines():
        fields = line.split("\t", 1)
        if len(fields) != 2:
            raise PolicyGate("REMOTE_REF_PROBE_MALFORMED")
        sha, ref = fields
        if (
            not object_format.matches(sha)
            or not ref.startswith("refs/heads/")
            or ref in heads
        ):
            raise PolicyGate("REMOTE_REF_PROBE_MALFORMED")
        heads[ref] = sha
    return heads


def _delete_temp(
    repo: GitRepository,
    ref: str,
    expected_shas: Sequence[str],
    object_format: GitObjectFormat,
) -> None:
    current_result = repo.run(["rev-parse", "--verify", ref], check=False)
    if current_result.returncode != 0:
        raise PolicyGate("TEMP_REF_CLEANUP_FAILED", ref=ref, reason="ref_missing")
    current = current_result.stdout.strip()
    if not object_format.matches(current) or current not in expected_shas:
        raise PolicyGate(
            "TEMP_REF_CLEANUP_FAILED",
            ref=ref,
            reason="unexpected_sha",
            observed_sha=current,
        )
    deleted = repo.run(["update-ref", "-d", ref, current], check=False)
    if deleted.returncode != 0:
        raise PolicyGate("TEMP_REF_CLEANUP_FAILED", ref=ref, reason="delete_failed")


def _reserve_temp_ref(
    repo: GitRepository, reservation_sha: str, object_format: GitObjectFormat
) -> str:
    temp_ref = f"refs/versionkeeping/publication/{secrets.token_hex(16)}"
    present = repo.run(["show-ref", "--verify", "--quiet", temp_ref], check=False)
    if present.returncode == 0:
        raise PolicyGate("TEMP_REF_COLLISION", ref=temp_ref)
    if present.returncode != 1:
        raise RuntimeError("unable to prove temporary ref absence")
    reserved = repo.run(
        ["update-ref", temp_ref, reservation_sha, "0" * object_format.oid_width],
    )
    if reserved.returncode != 0:
        raise PolicyGate("TEMP_REF_COLLISION", ref=temp_ref)
    return temp_ref


def _fetch_and_verify_ref(
    repo: GitRepository,
    endpoint: str,
    ref: str,
    expected: str,
    temp_ref: str,
    object_format: GitObjectFormat,
) -> None:
    result = _run_endpoint(
        repo,
        endpoint,
        [
            "-c",
            "maintenance.auto=false",
            "-c",
            "fetch.writeCommitGraph=false",
            "fetch",
            "--no-tags",
            "--no-write-fetch-head",
            "--no-recurse-submodules",
            "--no-auto-maintenance",
        ],
        [f"+{ref}:{temp_ref}"],
    )
    if result.returncode != 0:
        raise PolicyGate(
            "REMOTE_REF_CHANGED_DURING_FETCH", ref=ref, expected_sha=expected
        )
    fetched = repo.output(["rev-parse", "--verify", f"{temp_ref}^{{commit}}"])
    if not object_format.matches(fetched) or fetched != expected:
        raise PolicyGate(
            "REMOTE_REF_CHANGED_DURING_FETCH",
            ref=ref,
            expected_sha=expected,
            fetched_sha=fetched,
        )
    if _probe_ref(repo, endpoint, ref, object_format) != expected:
        raise PolicyGate(
            "REMOTE_REF_CHANGED_DURING_FETCH", ref=ref, expected_sha=expected
        )


def _cleanup_temp_after_error(
    repo: GitRepository,
    temp_ref: str,
    expected_shas: Sequence[str],
    error: BaseException,
    object_format: GitObjectFormat,
) -> None:
    try:
        _delete_temp(repo, temp_ref, expected_shas, object_format)
    except PolicyGate as cleanup_error:
        if isinstance(error, PolicyGate):
            error.evidence["cleanup_failure"] = {
                "code": cleanup_error.code,
                "evidence": cleanup_error.evidence,
            }
        else:
            raise cleanup_error from error


@contextmanager
def _stable_fetched_ref(
    repo: GitRepository,
    endpoint: str,
    ref: str,
    expected: str,
    reservation_sha: str,
    object_format: GitObjectFormat,
) -> Iterator[str]:
    temp_ref = _reserve_temp_ref(repo, reservation_sha, object_format)
    expected_shas = (reservation_sha, expected)
    try:
        _fetch_and_verify_ref(repo, endpoint, ref, expected, temp_ref, object_format)
        yield temp_ref
    except BaseException as error:
        _cleanup_temp_after_error(repo, temp_ref, expected_shas, error, object_format)
        raise
    else:
        _delete_temp(repo, temp_ref, expected_shas, object_format)


def _is_ancestor(repo: GitRepository, older: str, newer: str) -> bool:
    result = repo.run(["merge-base", "--is-ancestor", older, newer], check=False)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise RuntimeError("git merge-base failed")


def _rev_list(repo: GitRepository, expression: str) -> Tuple[str, ...]:
    value = repo.output(["rev-list", "--topo-order", "--reverse", expression, "--"])
    return tuple(value.splitlines()) if value else ()


def _config_digest(details: dict, fingerprint: str) -> str:
    safe = dict(details)
    safe["endpoint_fingerprint"] = fingerprint
    encoded = json.dumps(safe, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _snapshot(
    repo: GitRepository,
    request: PublicationRequest,
    object_format: GitObjectFormat,
) -> RepositorySnapshot:
    context = {}
    try:
        remote, ref, selection = _resolve_destination(repo, request)
        context["destination"] = {
            "remote": remote,
            "ref": ref,
            "endpoint_fingerprint": None,
            "config_digest": None,
        }
        endpoint, fingerprint = _endpoint(repo, remote)
        default_branch_ref = _probe_default_branch(repo, endpoint, object_format)
        selection = dict(selection)
        selection["default_branch_ref"] = default_branch_ref
        digest = _config_digest(selection, fingerprint)
        context["destination"].update(
            {
                "endpoint_fingerprint": fingerprint,
                "config_digest": digest,
                "default_branch_ref": default_branch_ref,
            }
        )
        target_sha = _probe_ref(repo, endpoint, ref, object_format)
        context["target"] = {"present": target_sha is not None, "sha": target_sha}
        start_is_ancestor = _is_ancestor(repo, request.start_head, request.source_sha)
        if target_sha is not None:
            with _stable_fetched_ref(
                repo, endpoint, ref, target_sha, request.source_sha, object_format
            ) as target_temp:
                outgoing = _rev_list(repo, f"{target_temp}..{request.source_sha}")
                target_only = _rev_list(repo, f"{request.source_sha}..{target_temp}")
                target_ancestor = _is_ancestor(repo, target_temp, request.source_sha)
            target = PresentTarget(
                sha=target_sha,
                outgoing_shas=outgoing,
                target_only_shas=target_only,
                is_ancestor=target_ancestor,
            )
        else:
            if _probe_ref(repo, endpoint, ref, object_format) is not None:
                raise PolicyGate("REMOTE_REF_APPEARED_DURING_PROBE", ref=ref)
            advertised = _advertised_heads(repo, endpoint, object_format)
            start_advertised = request.start_head in advertised.values()
            baseline = request.start_head
            creation_base = None
            if not start_advertised and request.creation_base_ref is not None:
                creation_base_sha = _probe_ref(
                    repo, endpoint, request.creation_base_ref, object_format
                )
                if creation_base_sha is not None:
                    creation_base_to_start = ()
                    with _stable_fetched_ref(
                        repo,
                        endpoint,
                        request.creation_base_ref,
                        creation_base_sha,
                        request.source_sha,
                        object_format,
                    ) as base_temp:
                        creation_base_is_ancestor = _is_ancestor(
                            repo, base_temp, request.start_head
                        )
                        if creation_base_is_ancestor:
                            creation_base_to_start = _rev_list(
                                repo, f"{base_temp}..{request.start_head}"
                            )
                            baseline = creation_base_sha
                    creation_base = CreationBase(
                        sha=creation_base_sha,
                        is_ancestor=creation_base_is_ancestor,
                        to_start_shas=creation_base_to_start,
                    )
            outgoing = _rev_list(repo, f"{baseline}..{request.source_sha}")
            target = AbsentTarget(
                outgoing_shas=outgoing,
                start_advertised=start_advertised,
                creation_base=creation_base,
            )
        return RepositorySnapshot(
            remote=remote,
            ref=ref,
            endpoint_fingerprint=fingerprint,
            config_digest=digest,
            default_branch_ref=default_branch_ref,
            target=target,
            start_is_ancestor=start_is_ancestor,
        )
    except PolicyGate as gate:
        gate.retain_context(context)
        raise


def plan_repository(path: Path, raw_request: Any) -> dict:
    request = (
        raw_request
        if isinstance(raw_request, PublicationRequest)
        else parse_request(raw_request)
    )
    with GitRepository(Path(path)) as repo:
        try:
            _guard_repository(repo)
            object_format = _object_format(repo)
            _bind_request_object_format(request, object_format)
            _ensure_commits(repo, request)
            return plan_publication(request, _snapshot(repo, request, object_format))
        except PolicyGate as gate:
            return _blocked(request, gate)
