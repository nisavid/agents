#!/usr/bin/env python3
"""Run bounded Artifact Customs behavior evaluations.

Fixture mode proves the runner contract without making provider claims.
Provider mode gives an executor only the task context and gives an independent
grader only the executor response plus the hidden rubric. Each adapter runs in
its own new private temporary root with a scrubbed environment. This is process
and workspace isolation, not an operating-system sandbox.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from evidence_transport import (  # noqa: E402
    AttemptSuccess,
    ProviderTransportFailure,
    allocate_attempt_journal,
    digest_bytes,
    json_file_bytes,
    prepare_private_directory,
    private_atomic_write,
    run_attempt_journal,
    safe_provider_failure_identity,
    safe_provider_failure_message,
    safe_regular_file,
    stream_identity,
    strict_json_bytes,
)


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "evals/artifact-customs/corpus.json"
SKILL_ROOT = ROOT / "plugins/artifact-customs/skills"
PUBLIC_SKILLS = (
    "assessing-third-party-components",
    "adopting-third-party-components",
    "maintaining-third-party-components",
)
SCENARIO_FIELDS = {
    "id",
    "skill",
    "fixture_path",
    "prompt",
    "required_outcome",
    "expectations",
}
MAX_CORPUS_FILE_BYTES = 1024 * 1024
MAX_REQUEST_BYTES = 512 * 1024
MAX_STREAM_BYTES = 1024 * 1024
ADAPTER_TIMEOUT_SECONDS = 30
REBIND_RESPONSE = r"(?:re-?bind|re-?resolve)"
FIXTURE_RESPONSE_CONTRACTS = {
    "exact-candidate-rebind-before-writes": (
        "rebind-before-writes",
        re.compile(
            rf"{REBIND_RESPONSE} the exact candidate immediately before the source write\."
        ),
    ),
    "policy-revoked-before-write": (
        "no-mutation-after-rebind-drift",
        re.compile(
            rf"{REBIND_RESPONSE} the policy identity immediately before the source "
            r"write\. no mutation is allowed\."
        ),
    ),
    "policy-drift-before-policy-write": (
        "no-mutation-after-rebind-drift",
        re.compile(
            rf"{REBIND_RESPONSE} the policy identity immediately before the policy "
            r"write\. no mutation is allowed\."
        ),
    ),
    "authority-drift-before-retained-evidence-write": (
        "no-mutation-after-rebind-drift",
        re.compile(
            rf"{REBIND_RESPONSE} the authority identity immediately before the "
            r"retained-?evidence write\. no mutation is allowed\."
        ),
    ),
    "head-drift-before-forge-close": (
        "no-mutation-after-rebind-drift",
        re.compile(
            r"the separately authorized exact forge action is close\. "
            rf"{REBIND_RESPONSE} the candidate, policy, and exact forge-action "
            r"authority identities immediately before forge close\. no mutation is "
            r"allowed\."
        ),
    ),
    "head-drift-before-forge-publish-approve-or-merge": (
        "no-mutation-after-rebind-drift",
        re.compile(
            rf"{REBIND_RESPONSE} the candidate, policy, and exact forge-action "
            r"authority identities immediately before forge publish, approve, and "
            r"merge\. no mutation is allowed\."
        ),
    ),
    "hard-no-go-without-forge-action-authority": (
        "no-forge-action-without-exact-authority",
        re.compile(
            r"a hard no-go grants no forge authority, so do not close or reject\. "
            r"separate authorization is required for the exact forge action, then "
            rf"{REBIND_RESPONSE} the candidate, policy, and authority immediately "
            r"before that action\."
        ),
    ),
    "codex-current-harness-onboarding": (
        "offer-current-harness-schedule",
        re.compile(
            r"inspect the existing or possible dependency or component maintenance "
            r"schedule or analogous process first\. offer best-effort integration or "
            r"alignment from available evidence\. because suitability is uncertain, "
            r"also offer a context-sensitive standalone cadence\. the operator selects "
            r"activation, cadence, and autonomymode\. artifact customs remains "
            r"scheduler-neutral and manual invocation remains available\."
        ),
    ),
    "out-of-scope-model-dataset": (
        "no-artifact-customs-route",
        re.compile(
            r"no artifact customs skill applies to this model or dataset request\."
        ),
    ),
    "out-of-scope-credentials-saas": (
        "no-artifact-customs-route",
        re.compile(
            r"no artifact customs skill applies to this credential or saas request\."
        ),
    ),
    "out-of-scope-release": (
        "no-artifact-customs-route",
        re.compile(r"no artifact customs skill applies to this first-party release\."),
    ),
    "out-of-scope-generic-pr": (
        "no-artifact-customs-route",
        re.compile(r"no artifact customs skill applies to this generic pull request\."),
    ),
}
FIXTURE_BEHAVIOR_CONTRACTS = {
    "claude-current-harness-onboarding": {
        "required_outcome": "offer-current-harness-schedule",
        "behaviors": (
            "claude_desktop_adapter",
            "user_selects_activation",
            "user_selects_cadence",
            "user_selects_autonomy_mode",
            "manual_remains",
            "no_scheduler_preference",
            "inspect_existing_or_possible_maintenance_process_first",
            "recommend_context_sensitive_cadence_when_none_found",
        ),
    },
}
FIXTURE_EXPECTATION_CONTRACTS = {
    "exact-candidate-rebind-before-writes": [
        "exact_candidate",
        "rebind_before_write",
        "no_stale_identity_write",
    ],
    "policy-revoked-before-write": [
        "policy_identity_rebound",
        "source_write_rebound",
        "zero_mutation",
        "no_stale_identity_write",
    ],
    "policy-drift-before-policy-write": [
        "policy_identity_rebound",
        "policy_write_rebound",
        "zero_mutation",
        "no_stale_identity_write",
    ],
    "authority-drift-before-retained-evidence-write": [
        "authority_identity_rebound",
        "retained_evidence_write_rebound",
        "zero_mutation",
        "no_stale_identity_write",
    ],
    "head-drift-before-forge-close": [
        "candidate_identity_rebound",
        "policy_identity_rebound",
        "authority_identity_rebound",
        "forge_close_rebound",
        "exact_forge_action_authority",
        "zero_mutation",
    ],
    "head-drift-before-forge-publish-approve-or-merge": [
        "candidate_identity_rebound",
        "policy_identity_rebound",
        "authority_identity_rebound",
        "forge_publish_approve_or_merge_rebound",
        "zero_mutation",
    ],
    "hard-no-go-without-forge-action-authority": [
        "hard_no_go",
        "separate_exact_forge_action_authority",
        "immediate_pre_forge_rebind",
        "zero_forge_mutation",
    ],
    "codex-current-harness-onboarding": [
        "codex_chatgpt_adapter",
        "user_selects_activation",
        "user_selects_cadence",
        "user_selects_autonomy_mode",
        "manual_remains",
        "no_scheduler_preference",
        "inspect_existing_or_possible_maintenance_process_first",
        "offer_best_effort_integration_or_alignment",
        "context_sensitive_standalone_cadence_when_uncertain",
    ],
    "claude-current-harness-onboarding": [
        "claude_desktop_adapter",
        "user_selects_activation",
        "user_selects_cadence",
        "user_selects_autonomy_mode",
        "manual_remains",
        "no_scheduler_preference",
        "inspect_existing_or_possible_maintenance_process_first",
        "recommend_context_sensitive_cadence_when_none_found",
    ],
    "out-of-scope-model-dataset": ["models_and_datasets_excluded"],
    "out-of-scope-credentials-saas": ["credentials_and_saas_excluded"],
    "out-of-scope-release": ["outbound_release_excluded"],
    "out-of-scope-generic-pr": ["generic_git_pr_review_excluded"],
}


class EvaluationError(RuntimeError):
    """Report an invalid evaluation input or provider result."""


class TransportFailure(ProviderTransportFailure, EvaluationError):
    """A safe public provider error retaining its exact private streams."""


def canonical(value: object) -> bytes:
    return json_file_bytes(value)


def digest(value: bytes) -> str:
    return digest_bytes(value)


def parse_verdict(payload: bytes) -> dict[str, Any]:
    """Parse the grader's exact public verdict contract."""
    verdict = strict_json_bytes(
        payload, label="grader verdict", error_factory=EvaluationError
    )
    if not isinstance(verdict, dict) or set(verdict) != {"passed", "failures"}:
        raise EvaluationError("grader verdict must contain only passed and failures")
    if type(verdict["passed"]) is not bool:
        raise EvaluationError("grader verdict passed must be a boolean")
    failures = verdict["failures"]
    if not isinstance(failures, list) or any(
        not isinstance(failure, str) or not failure for failure in failures
    ):
        raise EvaluationError(
            "grader verdict failures must be a list of non-empty strings"
        )
    if verdict["passed"] != (failures == []):
        raise EvaluationError(
            "grader verdict passed must agree with whether failures is empty"
        )
    return verdict


def decode_executor_response(payload: bytes) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise EvaluationError("executor response is not UTF-8") from error


def _read_bounded_file(path: Path, *, label: str) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise EvaluationError(f"{label} cannot be inspected") from error
    if size > MAX_CORPUS_FILE_BYTES:
        raise EvaluationError(f"{label} exceeds the corpus file size limit")
    try:
        return path.read_bytes()
    except OSError as error:
        raise EvaluationError(f"{label} cannot be read") from error


def _read_bounded_text(path: Path, *, label: str) -> str:
    try:
        return _read_bounded_file(path, label=label).decode("utf-8")
    except UnicodeDecodeError as error:
        raise EvaluationError(f"{label} is not UTF-8") from error


def _safe_text_file(root: Path, relative: Any, *, label: str) -> str:
    path = safe_regular_file(root, relative, label=label, error_factory=EvaluationError)
    return _read_bounded_text(path, label=label)


def _validate_scenario(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != SCENARIO_FIELDS:
        raise EvaluationError("Artifact Customs scenario schema is invalid")
    if not all(
        isinstance(value[field], str) and value[field]
        for field in ("id", "fixture_path", "prompt", "required_outcome")
    ):
        raise EvaluationError("Artifact Customs scenario text fields are invalid")
    skill = value["skill"]
    if skill is not None and skill not in PUBLIC_SKILLS:
        raise EvaluationError(f"unknown Artifact Customs skill: {skill!r}")
    expectations = value["expectations"]
    if (
        not isinstance(expectations, list)
        or not expectations
        or any(not isinstance(item, str) or not item for item in expectations)
    ):
        raise EvaluationError("Artifact Customs scenario expectations are invalid")
    return value


def _load_corpus(corpus_path: Path) -> dict[str, Any]:
    try:
        metadata = corpus_path.lstat()
    except OSError as error:
        raise EvaluationError("Artifact Customs corpus is unavailable") from error
    if corpus_path.is_symlink() or not corpus_path.is_file():
        raise EvaluationError("Artifact Customs corpus must be a regular non-symlink")
    if metadata.st_size > MAX_CORPUS_FILE_BYTES:
        raise EvaluationError("Artifact Customs corpus exceeds the size limit")
    corpus = strict_json_bytes(
        _read_bounded_file(corpus_path, label="Artifact Customs corpus"),
        label="Artifact Customs corpus",
        error_factory=EvaluationError,
    )
    if not isinstance(corpus, dict) or not isinstance(corpus.get("scenarios"), list):
        raise EvaluationError("Artifact Customs corpus schema is invalid")
    return corpus


def load_scenarios(
    selected: list[str],
    *,
    corpus_path: Path = CORPUS,
    skill_root: Path = SKILL_ROOT,
) -> list[dict[str, Any]]:
    if not selected:
        raise EvaluationError("select at least one Artifact Customs scenario")
    scenarios: dict[str, dict[str, Any]] = {}
    for value in _load_corpus(corpus_path)["scenarios"]:
        scenario = _validate_scenario(value)
        identifier = scenario["id"]
        if identifier in scenarios:
            raise EvaluationError(f"duplicate Artifact Customs scenario: {identifier}")
        load_case(
            scenario,
            corpus_root=corpus_path.parent,
            skill_root=skill_root,
        )
        scenarios[identifier] = scenario
    missing = set(selected) - set(scenarios)
    if missing:
        raise EvaluationError(f"unknown scenarios: {', '.join(sorted(missing))}")
    return [scenarios[identifier] for identifier in selected]


def load_case(
    scenario: dict[str, Any],
    *,
    corpus_root: Path = CORPUS.parent,
    skill_root: Path = SKILL_ROOT,
) -> dict[str, Any]:
    """Load only safe, bounded regular files for one executor request."""
    scenario = _validate_scenario(scenario)
    fixture = _safe_text_file(
        corpus_root,
        scenario["fixture_path"],
        label=f"{scenario['id']} fixture",
    )
    selected_skill = scenario["skill"]
    if selected_skill is None:
        skill_context: str | list[dict[str, str]] = [
            {
                "name": skill,
                "content": _safe_text_file(
                    skill_root,
                    f"{skill}/SKILL.md",
                    label=f"{skill} skill",
                ),
            }
            for skill in PUBLIC_SKILLS
        ]
        routing_mode = "discoverable"
    else:
        skill_context = _safe_text_file(
            skill_root,
            f"{selected_skill}/SKILL.md",
            label=f"{selected_skill} skill",
        )
        routing_mode = "direct"
    return {
        "fixture": fixture,
        "skill_context": skill_context,
        "routing_mode": routing_mode,
    }


def executor_request(
    scenario: dict[str, Any],
    fixture: str,
    skill_context: str | list[dict[str, str]],
) -> bytes:
    """Build the executor payload without target identity or adjudication rubric."""
    request: dict[str, Any] = {
        "prompt": scenario["prompt"],
        "fixture": fixture,
    }
    if isinstance(skill_context, str):
        request["skill"] = skill_context
    else:
        request["discoverable_skills"] = skill_context
    return canonical(request)


def grader_request(scenario: dict[str, Any], response: str) -> bytes:
    """Build the grader payload without raw fixture or skill text."""
    return canonical(
        {
            "scenario_id": scenario["id"],
            "response": response,
            "rubric": {
                "required_outcome": scenario["required_outcome"],
                "expectations": scenario["expectations"],
            },
        }
    )


CLAUDE_ONBOARDING_FIXTURE_EVIDENCE = {
    "claude_desktop_adapter": "adapter: claude code uses claude desktop",
    "user_selects_activation": "activation selection: the operator selects activation",
    "user_selects_cadence": "cadence selection: the operator selects cadence",
    "user_selects_autonomy_mode": "autonomymode selection: the operator selects autonomymode",
    "manual_remains": "manual invocation: remains available to the operator",
    "no_scheduler_preference": "scheduler: artifact customs has no preferred scheduler",
    "inspect_existing_or_possible_maintenance_process_first": (
        "inspection: first, the operator inspects the existing or possible dependency "
        "or component maintenance schedule or analogous process"
    ),
    "recommend_context_sensitive_cadence_when_none_found": (
        "cadence recommendation: if no schedule or analogous process is found, the "
        "operator recommends a context-sensitive cadence"
    ),
}


def _controlled_fixture_evidence(response: str) -> tuple[set[str], bool]:
    """Parse the Claude fixture's closed, behavior-specific evidence grammar."""
    has_terminal_lf = response.endswith("\n")
    raw_clauses = response.split("\n")
    if raw_clauses[-1:] == [""]:
        raw_clauses.pop()
    if not raw_clauses or any(not raw_clause for raw_clause in raw_clauses):
        return set(), True

    uses_crlf = [raw_clause.endswith("\r") for raw_clause in raw_clauses]
    if any(uses_crlf):
        crlf_records = raw_clauses if has_terminal_lf else raw_clauses[:-1]
        if (
            not crlf_records
            or not all(raw_clause.endswith("\r") for raw_clause in crlf_records)
            or (not has_terminal_lf and raw_clauses[-1].endswith("\r"))
        ):
            return set(), True
        raw_clauses = [
            raw_clause[:-1] if raw_clause.endswith("\r") else raw_clause
            for raw_clause in raw_clauses
        ]
    if any("\r" in raw_clause for raw_clause in raw_clauses):
        return set(), True

    recognized: set[str] = set()
    unknown_or_duplicate = False
    for raw_clause in raw_clauses:
        clause = re.sub(r"[ \t]+", " ", raw_clause.lower().strip(" \t"))
        if clause.endswith("."):
            clause = clause[:-1]
        behavior = next(
            (
                name
                for name, required_clause in CLAUDE_ONBOARDING_FIXTURE_EVIDENCE.items()
                if clause == required_clause
            ),
            None,
        )
        if behavior is None or behavior in recognized:
            unknown_or_duplicate = True
            continue
        recognized.add(behavior)
    return recognized, unknown_or_duplicate


def grade(scenario: dict[str, Any], response: str) -> dict[str, Any]:
    """Deterministic fixture-only grade for runner contract tests."""
    contract = FIXTURE_RESPONSE_CONTRACTS.get(scenario.get("id"))
    behavior_contract = FIXTURE_BEHAVIOR_CONTRACTS.get(scenario.get("id"))
    if contract is None and behavior_contract is None:
        return {"checked": False, "passed": None, "failures": []}
    failures: list[str] = []
    if contract is not None:
        expected_outcome, response_pattern = contract
    else:
        expected_outcome = behavior_contract["required_outcome"]
        response_pattern = None
    if scenario.get("required_outcome") != expected_outcome:
        failures.append("fixture grader required outcome drift")
    if scenario.get("expectations") != FIXTURE_EXPECTATION_CONTRACTS[scenario["id"]]:
        failures.append("fixture grader expectation coverage drift")
    normalized = " ".join(response.lower().split())
    if response_pattern is not None and response_pattern.fullmatch(normalized) is None:
        failures.append("response is outside the canonical fixture contract")
    if behavior_contract is not None:
        behavior_names = behavior_contract["behaviors"]
        if set(behavior_names) != set(scenario["expectations"]) or set(
            CLAUDE_ONBOARDING_FIXTURE_EVIDENCE
        ) != set(behavior_names):
            failures.append("fixture grader behavior coverage drift")
        else:
            recognized, unknown_or_duplicate = _controlled_fixture_evidence(response)
            missing_behaviors = [
                behavior
                for behavior in scenario["expectations"]
                if behavior not in recognized
            ]
            if missing_behaviors:
                failures.append("missing required response behaviors")
            if unknown_or_duplicate:
                failures.append("response is outside the controlled fixture grammar")
    return {"checked": True, "passed": not failures, "failures": failures}


def _resolve_executable(
    command: str, *, role: str, model: str
) -> tuple[list[str], dict]:
    if not isinstance(model, str) or not model.strip():
        raise EvaluationError(f"{role} model identity is required")
    try:
        arguments = shlex.split(command)
    except ValueError as error:
        raise EvaluationError(f"{role} command is invalid") from error
    if not arguments:
        raise EvaluationError(f"{role} command is empty")
    if arguments.count("{model}") != 1:
        raise EvaluationError(
            f"{role} command must bind the model through one {{model}} argument"
        )
    if (
        any(character in model for character in ("\x00", "\r", "\n"))
        or str(ROOT.resolve()) in model
    ):
        raise EvaluationError(f"{role} model identity is unsafe")
    arguments = [model if argument == "{model}" else argument for argument in arguments]
    located = shutil.which(arguments[0])
    if located is None:
        raise EvaluationError(f"{role} executable was not found")
    try:
        executable = Path(located).resolve(strict=True)
    except OSError as error:
        raise EvaluationError(f"{role} executable cannot be resolved") from error
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise EvaluationError(f"{role} executable is not executable")
    repository_text = str(ROOT.resolve())
    if executable.is_relative_to(ROOT.resolve()) or any(
        repository_text in argument for argument in arguments[1:]
    ):
        raise EvaluationError(f"{role} command leaks the repository path")
    arguments[0] = str(executable)
    argument_files = []
    for index, argument in enumerate(arguments[1:], start=1):
        argument_path = Path(argument)
        if not argument_path.is_absolute() or not argument_path.is_file():
            continue
        try:
            resolved_argument = argument_path.resolve(strict=True)
        except OSError as error:
            raise EvaluationError(
                f"{role} adapter file argument cannot be resolved"
            ) from error
        if resolved_argument.is_relative_to(ROOT.resolve()):
            raise EvaluationError(f"{role} command leaks the repository path")
        arguments[index] = str(resolved_argument)
        argument_files.append(
            {
                "path": str(resolved_argument),
                "sha256": _executable_digest(resolved_argument, role=role),
            }
        )
    executable_identity = {
        "path": str(executable),
        "sha256": _executable_digest(executable, role=role),
    }
    identity = {
        "adapter": "command-json-stdio-v1",
        "role": role,
        "model": model,
        "executable": executable_identity,
        "argv_sha256": digest(canonical(arguments)),
        "argument_count": len(arguments),
        "argument_files": argument_files,
    }
    return arguments, identity


def _executable_digest(path: Path, *, role: str) -> str:
    try:
        with path.open("rb") as executable:
            hasher = hashlib.sha256()
            while True:
                chunk = executable.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
    except OSError as error:
        raise EvaluationError(f"{role} executable cannot be hashed") from error
    return "sha256:" + hasher.hexdigest()


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
    except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def _bounded_process(
    arguments: list[str],
    request: bytes,
    *,
    work: Path,
    environment: dict[str, str],
    role: str,
) -> tuple[bytes, bytes]:
    if len(request) > MAX_REQUEST_BYTES:
        raise EvaluationError("adapter request exceeds the input size limit")
    request_path = work.parent / "request.json"
    private_atomic_write(request_path, request, error_factory=EvaluationError)
    try:
        request_file = request_path.open("rb")
    except OSError as error:
        raise EvaluationError("adapter request cannot be opened") from error
    try:
        try:
            process = subprocess.Popen(
                arguments,
                cwd=work,
                env=environment,
                stdin=request_file,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                start_new_session=True,
                umask=0o077,
            )
        except OSError as error:
            raise TransportFailure(
                safe_provider_failure_message(
                    role=role,
                    stderr=b"",
                    reason="could not start",
                )
            ) from error
        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        streams: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
        sizes = {"stdout": 0, "stderr": 0}
        deadline = time.monotonic() + ADAPTER_TIMEOUT_SECONDS
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _terminate_process_group(process)
                    raise TransportFailure(
                        safe_provider_failure_message(
                            role=role,
                            stderr=b"".join(streams["stderr"]),
                            timed_out=True,
                        ),
                        stdout=b"".join(streams["stdout"]),
                        stderr=b"".join(streams["stderr"]),
                        timed_out=True,
                    )
                events = selector.select(timeout=remaining)
                for key, _mask in events:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    name = key.data
                    sizes[name] += len(chunk)
                    streams[name].append(chunk)
                    if sizes[name] > MAX_STREAM_BYTES:
                        _terminate_process_group(process)
                        raise TransportFailure(
                            safe_provider_failure_message(
                                role=role,
                                stderr=b"".join(streams["stderr"]),
                                reason="stream size limit exceeded",
                            ),
                            stdout=b"".join(streams["stdout"]),
                            stderr=b"".join(streams["stderr"]),
                        )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process_group(process)
                raise TransportFailure(
                    safe_provider_failure_message(
                        role=role,
                        stderr=b"".join(streams["stderr"]),
                        timed_out=True,
                    ),
                    stdout=b"".join(streams["stdout"]),
                    stderr=b"".join(streams["stderr"]),
                    timed_out=True,
                )
            try:
                returncode = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired as error:
                _terminate_process_group(process)
                raise TransportFailure(
                    safe_provider_failure_message(
                        role=role,
                        stderr=b"".join(streams["stderr"]),
                        timed_out=True,
                    ),
                    stdout=b"".join(streams["stdout"]),
                    stderr=b"".join(streams["stderr"]),
                    timed_out=True,
                ) from error
        finally:
            selector.close()
            process.stdout.close()
            process.stderr.close()
        stdout = b"".join(streams["stdout"])
        stderr = b"".join(streams["stderr"])
        if returncode:
            raise TransportFailure(
                safe_provider_failure_message(
                    role=role,
                    stderr=stderr,
                    returncode=returncode,
                ),
                stdout=stdout,
                stderr=stderr,
                returncode=returncode,
            )
        return stdout, stderr
    finally:
        request_file.close()


def run_isolated_adapter(
    command: str,
    request: bytes,
    *,
    role: str,
    model: str,
    attempt_root: Path | None = None,
    validator: Callable[[bytes], Any] | None = None,
) -> dict[str, Any]:
    """Run one adapter in a new process and a new private workspace."""
    arguments, identity = _resolve_executable(command, role=role, model=model)
    executable = Path(identity["executable"]["path"])
    with tempfile.TemporaryDirectory(prefix=f"artifact-customs-{role}-") as temporary:
        workspace_root = Path(temporary).resolve()
        os.chmod(workspace_root, 0o700)
        work = workspace_root / "work"
        home = workspace_root / "home"
        temporary_root = workspace_root / "tmp"
        configuration = workspace_root / "config"
        cache = workspace_root / "cache"
        for directory in (work, home, temporary_root, configuration, cache):
            prepare_private_directory(directory, error_factory=EvaluationError)
        environment = {
            "HOME": str(home),
            "TMPDIR": str(temporary_root),
            "TMP": str(temporary_root),
            "TEMP": str(temporary_root),
            "XDG_CONFIG_HOME": str(configuration),
            "XDG_CACHE_HOME": str(cache),
            "PATH": f"{executable.parent}:/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": "C",
            "LC_ALL": "C",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }

        def invoke() -> dict[str, Any]:
            stdout, stderr = _bounded_process(
                arguments,
                request,
                work=work,
                environment=environment,
                role=role,
            )
            try:
                if (
                    _executable_digest(executable, role=role)
                    != identity["executable"]["sha256"]
                ):
                    raise EvaluationError(
                        f"{role} executable changed during evaluation"
                    )
                for argument_file in identity["argument_files"]:
                    if (
                        _executable_digest(Path(argument_file["path"]), role=role)
                        != argument_file["sha256"]
                    ):
                        raise EvaluationError(
                            f"{role} adapter file changed during evaluation"
                        )
                validated = validator(stdout) if validator is not None else None
            except Exception as error:
                raise TransportFailure(
                    str(error), stdout=stdout, stderr=stderr
                ) from error
            return {
                "stdout": stdout,
                "stderr": stderr,
                "validated": validated,
            }

        attempt_id: str | None = None
        attempt_document: dict[str, Any] | None = None
        if attempt_root is None:
            streams = invoke()
        else:
            prepare_private_directory(attempt_root, error_factory=EvaluationError)
            attempt_id = f"{role}-{uuid.uuid4()}"
            allocation = allocate_attempt_journal(
                attempt_root,
                attempt_relpath=f"attempts/{attempt_id}.json",
                stream_relpaths={
                    "stdout": f"streams/{attempt_id}.stdout.bin",
                    "stderr": f"streams/{attempt_id}.stderr.bin",
                },
                error_factory=EvaluationError,
            )

            def invoke_attempt() -> AttemptSuccess:
                result = invoke()
                return AttemptSuccess(
                    value=result,
                    streams={
                        "stdout": result["stdout"],
                        "stderr": result["stderr"],
                    },
                    fields={"returncode": 0, "timed_out": False},
                )

            def write_document(path: Path, value: dict[str, Any]) -> None:
                private_atomic_write(
                    path,
                    json_file_bytes(value),
                    error_factory=EvaluationError,
                )

            def failure_fields(
                error: BaseException, _classification: str
            ) -> dict[str, Any]:
                identity = safe_provider_failure_identity(error, role=role)
                return {
                    "failure_identity": identity,
                    "returncode": identity["returncode"],
                    "timed_out": identity["timed_out"],
                }

            streams, attempt_document = run_attempt_journal(
                allocation,
                initial={
                    "attempt_id": attempt_id,
                    "model": model,
                    "role": role,
                },
                invoke=invoke_attempt,
                document_writer=write_document,
                artifact_writer=lambda path, content: private_atomic_write(
                    path, content, error_factory=EvaluationError
                ),
                clock=lambda: datetime.now(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                ),
                status_names={
                    "started": "started",
                    "success": "completed",
                    "failure": "failed",
                    "timeout": "timeout",
                },
                stream_fields={
                    "stdout": ("stdout_relpath", "stdout_sha256"),
                    "stderr": ("stderr_relpath", "stderr_sha256"),
                },
                digest=digest_bytes,
                failure_fields=failure_fields,
            )
        stdout = streams["stdout"]
        stderr = streams["stderr"]
        workspace = {
            "working_directory": str(work),
            "home_directory": str(home),
            "temporary_directory": str(temporary_root),
        }
        result = {
            "stdout": stdout,
            "stderr": stderr,
            "stdout_identity": stream_identity(stdout),
            "stderr_identity": stream_identity(stderr),
            "validated": streams["validated"],
            "adapter_identity": identity,
            "workspace": workspace,
            "workspace_sha256": digest(canonical(workspace)),
            "environment_keys": sorted(environment),
            "containment": {
                "process": "separate",
                "workspace": "new-private-temporary-root",
                "environment": "scrubbed-minimal",
                "os_sandbox": "not-claimed",
            },
        }
        if attempt_id is not None and attempt_document is not None:
            result["attempt"] = {
                "attempt_id": attempt_id,
                "journal_sha256": digest_bytes(json_file_bytes(attempt_document)),
                "status": attempt_document["status"],
            }
        return result


def _request_separation(executor: bytes, grader: bytes) -> dict[str, Any]:
    executor_value = strict_json_bytes(
        executor, label="executor request", error_factory=EvaluationError
    )
    grader_value = strict_json_bytes(
        grader, label="grader request", error_factory=EvaluationError
    )
    if not isinstance(executor_value, dict) or not isinstance(grader_value, dict):
        raise EvaluationError("executor and grader requests must be objects")
    executor_fields = set(executor_value)
    grader_fields = set(grader_value)
    result = {
        "executor_visible_fields": sorted(executor_fields),
        "grader_visible_fields": sorted(grader_fields),
        "executor_has_hidden_rubric": bool(
            {"required_outcome", "expectations", "rubric"} & executor_fields
        ),
        "grader_has_fixture_or_skill": bool(
            {"fixture", "discoverable_skills", "skill"} & grader_fields
        ),
    }
    result["passed"] = not (
        result["executor_has_hidden_rubric"] or result["grader_has_fixture_or_skill"]
    )
    if not result["passed"]:
        raise EvaluationError("executor/grader request separation failed")
    return result


def _load_fixture_responses(path: Path) -> dict[str, str]:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise EvaluationError("fixture responses are unavailable") from error
    if (
        path.is_symlink()
        or not path.is_file()
        or metadata.st_size > MAX_CORPUS_FILE_BYTES
    ):
        raise EvaluationError("fixture responses must be a bounded regular non-symlink")
    value = strict_json_bytes(
        _read_bounded_file(path, label="fixture responses"),
        label="fixture responses",
        error_factory=EvaluationError,
    )
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(response, str)
        for key, response in value.items()
    ):
        raise EvaluationError("fixture responses must map scenario ids to text")
    return value


def _public_adapter_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in result.items()
        if key not in {"stdout", "stderr", "validated", "workspace"}
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", action="append", required=True)
    parser.add_argument("--adapter", choices=("fixture", "provider"), required=True)
    parser.add_argument("--fixture-responses", type=Path)
    parser.add_argument(
        "--executor-command",
        "--provider-command",
        dest="provider_command",
    )
    parser.add_argument(
        "--executor-model",
        "--provider-model",
        dest="provider_model",
    )
    parser.add_argument("--grader-command")
    parser.add_argument("--grader-model")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        try:
            output = (
                arguments.output.parent.resolve(strict=True) / arguments.output.name
            )
        except OSError as error:
            raise EvaluationError("evaluation output parent is unavailable") from error
        scenarios = load_scenarios(arguments.scenario)
        if arguments.adapter == "fixture":
            if arguments.fixture_responses is None:
                raise EvaluationError("fixture adapter requires --fixture-responses")
            responses = _load_fixture_responses(arguments.fixture_responses)
            execution_mode = "fixture-contract-only"
        else:
            if not all(
                (
                    arguments.provider_command,
                    arguments.provider_model,
                    arguments.grader_command,
                    arguments.grader_model,
                )
            ):
                raise EvaluationError(
                    "provider adapter requires executor/grader commands and models"
                )
            responses = {}
            execution_mode = "provider-process-and-workspace-isolated"
            attempt_root = output.parent / f".{output.name}.provider-attempts"
        runs = []
        for scenario in scenarios:
            loaded = load_case(scenario)
            execute = executor_request(
                scenario, loaded["fixture"], loaded["skill_context"]
            )
            executor_result = None
            grader_result = None
            if arguments.adapter == "fixture":
                try:
                    response = responses[scenario["id"]]
                except KeyError as error:
                    raise EvaluationError(
                        f"fixture response is missing: {scenario['id']}"
                    ) from error
            else:
                executor_result = run_isolated_adapter(
                    arguments.provider_command,
                    execute,
                    role="executor",
                    model=arguments.provider_model,
                    attempt_root=attempt_root,
                    validator=decode_executor_response,
                )
                response = executor_result["validated"]
            grader = grader_request(scenario, response)
            separation = _request_separation(execute, grader)
            if arguments.adapter == "fixture":
                verdict = grade(scenario, response)
            else:
                grader_result = run_isolated_adapter(
                    arguments.grader_command,
                    grader,
                    role="grader",
                    model=arguments.grader_model,
                    attempt_root=attempt_root,
                    validator=parse_verdict,
                )
                verdict = {
                    "checked": True,
                    **grader_result["validated"],
                }
            run = {
                "scenario_id": scenario["id"],
                "routing_mode": loaded["routing_mode"],
                "executor_request_sha256": digest(execute),
                "response_sha256": digest(response.encode()),
                "grader_request_sha256": digest(grader),
                "request_separation": separation,
                "grade_status": (
                    "checked" if verdict["checked"] else "transport-only-unchecked"
                ),
                "passed": verdict["passed"],
                "failures": verdict["failures"],
            }
            if executor_result is not None and grader_result is not None:
                run["executor_adapter"] = _public_adapter_result(executor_result)
                run["grader_adapter"] = _public_adapter_result(grader_result)
                if (
                    executor_result["workspace_sha256"]
                    == grader_result["workspace_sha256"]
                ):
                    raise EvaluationError(
                        "executor and grader workspaces are not distinct"
                    )
            runs.append(run)
        has_checked_failure = any(
            run["grade_status"] == "checked" and run["passed"] is False for run in runs
        )
        has_unchecked_run = any(
            run["grade_status"] == "transport-only-unchecked" for run in runs
        )
        if has_checked_failure:
            behavioral_status = "failed"
            behavioral_passed: bool | None = False
        elif has_unchecked_run:
            behavioral_status = "unchecked"
            behavioral_passed = None
        else:
            behavioral_status = "passed"
            behavioral_passed = True
        evidence = {
            "schema_version": 3,
            "execution_mode": execution_mode,
            "transport_passed": True,
            "behavioral_status": behavioral_status,
            "passed": behavioral_passed,
            "runs": runs,
        }
        private_atomic_write(output, canonical(evidence), error_factory=EvaluationError)
        return 2 if has_checked_failure else 0
    except (
        EvaluationError,
        KeyError,
        OSError,
        ProviderTransportFailure,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ) as error:
        print(f"Artifact Customs behavior evaluation failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
