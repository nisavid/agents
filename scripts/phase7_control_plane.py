#!/usr/bin/env python3
"""Deterministic, disposable operational fixtures for Phase 7.

The harness drives real local publisher and validator scripts against fake
executables.  It proves only those local process paths; it does not claim
natural-language dispatch, model selection, or a live GitHub interaction.
"""

from __future__ import annotations

import hashlib
import json
import platform
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CHANGE_NAVIGATION_SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "plugins/mergecraft/skills/writing-reviewable-pr-descriptions/scripts"
)
if str(CHANGE_NAVIGATION_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(CHANGE_NAVIGATION_SCRIPTS))
from change_navigation.git_observer import observe_git_diff  # noqa: E402


class ControlPlaneError(RuntimeError):
    """A deterministic Phase 7 fixture did not reach its declared state."""


@dataclass(frozen=True)
class ContractRoute:
    """A fixture declaration, not an inference about model dispatch."""

    intent: str
    owner: str
    mode: str


@dataclass(frozen=True)
class PublicationCapture:
    """One exact executable publisher command."""

    arguments: tuple[str, ...]


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def writer_validator_inputs(validator: Path) -> tuple[Path, ...]:
    """Bind the validator and every local Python module it imports directly."""
    navigation = validator.parent / "change_navigation"
    modules = tuple(sorted(navigation.rglob("*.py")))
    if not validator.is_file() or not modules:
        raise ControlPlaneError("writer validator import tree is incomplete")
    return (validator, *modules)


def resolve_contract_route(
    intent: str, routes: tuple[ContractRoute, ...]
) -> ContractRoute:
    """Resolve an exact fixture declaration without model simulation."""
    matches = [route for route in routes if route.intent == intent]
    if len(matches) != 1:
        raise ControlPlaneError(
            f"contract routing is missing or ambiguous for {intent!r}"
        )
    return matches[0]


def render_writer_fixture(pr_number: int | str) -> str:
    """Render a bounded body from canonical navigation vocabulary.

    The public writer currently exposes validators and navigation primitives,
    rather than a general renderer.  This narrow deterministic fixture uses
    those primitives and is always validated by the real validator before it
    is used as publisher input.
    """
    writer_scripts = (
        Path(__file__).parents[1]
        / "plugins/mergecraft/skills/writing-reviewable-pr-descriptions/scripts"
    )
    if str(writer_scripts) not in sys.path:
        sys.path.insert(0, str(writer_scripts))
    from change_navigation.categories import TAXONOMY_NOTE, category_title

    anchor = hashlib.sha256(b"src/widget.ts").hexdigest()

    def badge(
        alt: str,
        path: str,
        *,
        title: str | None = None,
        style: str = "flat",
        label_color: str | None = None,
    ) -> str:
        title_attribute = f' title="{title}"' if title else ""
        query = f"style={style}"
        if label_color is not None:
            query += f"&labelColor={label_color}"
        return (
            f'<picture><img alt="{alt}"{title_attribute} '
            f'src="https://img.shields.io/badge/{path}?{query}" height="16"></picture>'
        )

    metric = badge(
        "IMPL: 9 additions, 3 deletions",
        "IMPL-%2B9%20%E2%88%923-0969DA",
        title=category_title("IMPL", 9, 3),
    )
    summary = " ".join(
        (
            badge("DIFF", "DIFF-57606A", style="for-the-badge"),
            metric,
            badge("FILES: 1 touched", "FILES-1-5F6B78"),
        )
    ).replace("</picture> ", "</picture>&nbsp;", 1)
    atomic = badge(
        "9 additions, 3 deletions",
        "%2B9-%E2%88%923-CF222E",
        title="9 additions, 3 deletions",
        label_color="1A7F37",
    )
    return "\n".join(
        (
            "<details>",
            f"<summary>{summary}</summary>",
            "",
            f"- {metric} {badge('FILES: 1 implementation file', 'FILES-1-5F6B78')}",
            f"  - [`src/widget.ts`](https://github.com/acme/app/pull/{pr_number}/files#diff-{anchor}) {atomic}",
            "",
            TAXONOMY_NOTE,
            "",
            "</details>",
            "",
            "## Summary",
            "- Add the widget.",
            "",
        )
    )


def build_new_draft_argv(
    *,
    publisher: Path,
    repository: str,
    base: str,
    base_oid: str,
    head: str,
    head_oid: str,
    head_owner: str,
    head_repository: str,
    title: str,
    body_template: Path,
    review_input: Path,
    review_mode: str,
    selected_specialists: list[str],
) -> list[str]:
    """Build the exact executable new-draft publisher argv once."""
    if review_mode not in {"required", "not-required"}:
        raise ControlPlaneError("review mode must be required or not-required")
    if (
        not isinstance(selected_specialists, list)
        or not all(
            isinstance(specialist, str) and specialist
            for specialist in selected_specialists
        )
        or selected_specialists != sorted(set(selected_specialists))
    ):
        raise ControlPlaneError("selected review specialists must be sorted and unique")
    return [
        sys.executable,
        str(publisher),
        "--repository",
        repository,
        "--base",
        base,
        "--base-oid",
        base_oid,
        "--head",
        head,
        "--head-oid",
        head_oid,
        "--head-owner",
        head_owner,
        "--head-repository",
        head_repository,
        "--title",
        title,
        "--body-template",
        str(body_template),
        "--review-input",
        str(review_input),
        "--review-mode",
        review_mode,
        "--selected-specialists",
        canonical_bytes(selected_specialists).decode("utf-8"),
    ]


def capture_new_draft_command(**kwargs: Any) -> PublicationCapture:
    """Capture one new-draft argv for later owned-helper execution."""
    arguments = tuple(build_new_draft_argv(**kwargs))
    return PublicationCapture(arguments)


def write_new_draft_fixture(
    directory: Path,
    *,
    title: str,
    git_repository: Path,
    base_oid: str,
    head_oid: str,
) -> tuple[Path, Path]:
    """Write a validator-bound token template and review input for local tests."""
    token = "__PUBLISHING_REVIEWABLE_PRS_PR_NUMBER__"
    template = directory / "candidate-template.md"
    body = render_writer_fixture(token)
    template.write_text(body, encoding="utf-8")
    git_diff = observe_git_diff(git_repository, base_oid=base_oid, head_oid=head_oid)
    review_input: dict[str, object] = {
        "version": 3,
        "repository": "acme/app",
        "pr_number": token,
        "base": {"ref": "main", "oid": base_oid},
        "head": {
            "ref": "acme:widget",
            "oid": head_oid,
            "owner": "acme",
            "repository": "acme/app-fork",
        },
        "candidate": {
            "title": title,
            "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        },
        "git_diff": git_diff,
        "diff": [
            {
                "category": "IMPL",
                "operation": "BINARY"
                if row["binary"]
                else "MOVED"
                if row["operation"] == "renamed"
                else "COPIED"
                if row["operation"] == "copied"
                else "ATOMIC",
                "source_path": row["source_path"],
                "target_path": row["target_path"],
                "additions": row["additions"] or 0,
                "deletions": row["deletions"] or 0,
            }
            for row in git_diff
        ],
        "stack": [],
        "baseline": {
            "mode": "new",
            "title_sha256": None,
            "body_sha256": None,
            "fragments": [],
        },
    }
    review_input["content_sha256"] = hashlib.sha256(
        canonical_bytes(review_input)
    ).hexdigest()
    review_path = directory / "new-review-input.json"
    review_path.write_text(json.dumps(review_input), encoding="utf-8")
    return template, review_path


def build_updater_argv(
    *,
    updater: Path,
    review_input: Path,
    repository: str = "acme/app",
    pr_number: int = 2,
) -> list[str]:
    """Build the exact ready-state updater argv used by projection probes."""
    return [
        sys.executable,
        str(updater),
        "ready",
        "--repository",
        repository,
        "--pr",
        str(pr_number),
        "--base",
        "main",
        "--base-oid",
        "a" * 40,
        "--head",
        "acme:widget",
        "--head-oid",
        "b" * 40,
        "--head-owner",
        "acme",
        "--head-repository",
        "acme/app-fork",
        "--expected-title-sha256",
        "c" * 64,
        "--expected-body-sha256",
        "d" * 64,
        "--review-input",
        str(review_input),
    ]


def write_fake_gh(path: Path) -> None:
    """Write a JSON-state fake for the publisher's list/create/view/edit calls."""
    source = f"""#!{sys.executable}
import json
import os
import re
import sys
from pathlib import Path

state_path = Path(os.environ["PHASE7_GITHUB_STATE"])
state = json.loads(state_path.read_text(encoding="utf-8"))
arguments = sys.argv[1:]
state.setdefault("calls", []).append(arguments)
state.setdefault("effects", [])
state.setdefault("prs", [])
state.setdefault("next_number", 2)
identity = state.setdefault("identity", {{"base_oid": "a" * 40, "head_oid": "b" * 40}})

def write():
    state_path.write_text(json.dumps(state, sort_keys=True) + "\\n", encoding="utf-8")

def value(option):
    return arguments[arguments.index(option) + 1]

def pr(number):
    for item in state["prs"]:
        if item["number"] == number:
            return item
    raise SystemExit("unknown PR")

def rest(item):
    return {{
        "number": item["number"], "html_url": item["url"], "title": item["title"],
        "body": item["body"], "draft": item["isDraft"], "state": item["state"].lower(),
        "base": {{"ref": item["baseRefName"], "sha": item["baseRefOid"], "repo": {{"full_name": "acme/app"}}}},
        "head": {{"ref": item["headRefName"], "sha": item["headRefOid"], "repo": {{"full_name": item["headRepository"]["nameWithOwner"], "owner": item["headRepositoryOwner"]}}}},
    }}

def apply_sequence_fault(operation):
    fault = state.get("fault")
    if operation == "edit" and fault == "concurrent-body-before-write":
        pr(int(arguments[arguments.index("edit") + 1]))["body"] = "concurrent body"
    if operation == "edit" and fault == "restack-before-write":
        pr(int(arguments[arguments.index("edit") + 1]))["headRefOid"] = "c" * 40

def strict_object(raw):
    def unique(pairs):
        result = {{}}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result
    return json.loads(raw, object_pairs_hook=unique)

def graphql_request():
    if "--input" in arguments:
        source = value("--input")
        if source != "-":
            return None
        raw = sys.stdin.read()
        state.setdefault("stdin", []).append(raw)
        try:
            payload = strict_object(raw)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(payload, dict) or not isinstance(payload.get("query"), str):
            return None
        if not set(payload) <= {{"query", "variables", "operationName"}}:
            return None
        if "variables" in payload and not isinstance(payload["variables"], dict):
            return None
        operation_name = payload.get("operationName")
        if operation_name is not None and (
            not isinstance(operation_name, str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", operation_name) is None
        ):
            return None
        return payload["query"], operation_name
    fields = {{}}
    for index, argument in enumerate(arguments[:-1]):
        if argument in {{"-f", "-F", "--field", "--raw-field"}}:
            key, separator, item = arguments[index + 1].partition("=")
            if separator and key in {{"query", "operationName"}}:
                if key in fields:
                    return None
                fields[key] = item
    query = fields.get("query")
    operation_name = fields.get("operationName")
    if not isinstance(query, str) or (
        operation_name is not None
        and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", operation_name) is None
    ):
        return None
    return query, operation_name

def matching_brace(document, start):
    depth = 0
    for index in range(start, len(document)):
        if document[index] == "{{":
            depth += 1
        elif document[index] == "}}":
            depth -= 1
            if depth == 0:
                return index
    return None

def selection_start(document, start):
    parentheses = 0
    brackets = 0
    for index in range(start, len(document)):
        token = document[index]
        if token == "(":
            parentheses += 1
        elif token == ")":
            parentheses -= 1
        elif token == "[":
            brackets += 1
        elif token == "]":
            brackets -= 1
        elif token == "{{" and parentheses == 0 and brackets == 0:
            return index
        if parentheses < 0 or brackets < 0:
            return None
    return None

def parse_operations(document):
    lexical = re.sub(r'"{{3}}(?:.|\\n)*?"{{3}}|"(?:\\\\.|[^"\\\\])*"', ' ', document)
    lexical = re.sub(r"#[^\\n]*", " ", lexical)
    operations = []
    position = 0
    while position < len(lexical):
        whitespace = re.match(r"[\\s,]*", lexical[position:])
        position += whitespace.end()
        if position >= len(lexical):
            break
        if lexical[position] == "{{":
            kind = "query"
            name = None
            start = position
        else:
            definition = re.match(
                r"(query|mutation|subscription|fragment)\\b", lexical[position:]
            )
            if definition is None:
                return None
            kind = definition.group(1)
            cursor = position + definition.end()
            whitespace = re.match(r"\\s*", lexical[cursor:])
            cursor += whitespace.end()
            name_match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", lexical[cursor:])
            name = name_match.group(0) if name_match is not None else None
            if name_match is not None:
                cursor += name_match.end()
            start = selection_start(lexical, cursor)
            if start is None:
                return None
        end = matching_brace(lexical, start)
        if end is None:
            return None
        if kind != "fragment":
            operations.append(
                {{"kind": kind, "name": name, "body": lexical[start : end + 1]}}
            )
        position = end + 1
    names = [item["name"] for item in operations if item["name"] is not None]
    if len(names) != len(set(names)):
        return None
    return operations

def selected_operation(document, operation_name):
    operations = parse_operations(document)
    if operations is None or not operations:
        return None
    if operation_name is None:
        return operations[0] if len(operations) == 1 else None
    matches = [item for item in operations if item["name"] == operation_name]
    return matches[0] if len(matches) == 1 else None

def mutation_root_fields(body):
    fields = []
    depth = 0
    parentheses = 0
    position = 0
    while position < len(body):
        if body.startswith("...", position) and depth == 1:
            return None
        token = body[position]
        if token == "{{":
            depth += 1
            position += 1
            continue
        if token == "}}":
            depth -= 1
            position += 1
            continue
        if depth == 1 and token == "(":
            parentheses += 1
            position += 1
            continue
        if depth == 1 and token == ")":
            parentheses -= 1
            position += 1
            continue
        if depth == 1 and parentheses == 0:
            name = re.match(r"[A-Za-z_][A-Za-z0-9_]*", body[position:])
            if name is not None:
                field = name.group(0)
                position += name.end()
                whitespace = re.match(r"\\s*", body[position:])
                position += whitespace.end()
                if position < len(body) and body[position] == ":":
                    position += 1
                    whitespace = re.match(r"\\s*", body[position:])
                    position += whitespace.end()
                    target = re.match(r"[A-Za-z_][A-Za-z0-9_]*", body[position:])
                    if target is None:
                        return None
                    field = target.group(0)
                    position += target.end()
                fields.append(field)
                continue
        position += 1
    return fields

def classify_graphql():
    request = graphql_request()
    if request is None:
        return {{"classification": "unknown", "operation": "github-graphql-unknown"}}
    query, operation_name = request
    operation = selected_operation(query, operation_name)
    if operation is None:
        return {{"classification": "unknown", "operation": "github-graphql-unknown"}}
    if operation["kind"] == "query":
        return {{"classification": "read", "operation": "github-graphql-query"}}
    if operation["kind"] != "mutation":
        return {{"classification": "unknown", "operation": "github-graphql-unknown"}}
    mutations = {{
        "addComment": "comment-create",
        "addPullRequestReviewComment": "review-comment-create",
        "addPullRequestReviewThreadReply": "review-reply",
        "addReaction": "reaction-create",
        "addLabelsToLabelable": "labels-apply",
        "addPullRequestReview": "review-submit",
        "resolveReviewThread": "review-thread-resolution",
        "mergePullRequest": "merge",
    }}
    fields = mutation_root_fields(operation["body"])
    if fields is None:
        return {{"classification": "unknown", "operation": "github-graphql-unknown"}}
    matches = {{name for token, name in mutations.items() if token in fields}}
    if len(matches) != 1:
        return {{"classification": "unknown", "operation": "github-graphql-unknown"}}
    return {{"classification": "write", "operation": matches.pop()}}

def normalized_effect():
    if "api" in arguments:
        method = value("--method") if "--method" in arguments else "GET"
        endpoint = next((item for item in reversed(arguments) if item.startswith("repos/") or item == "graphql"), "")
        if endpoint == "graphql":
            return classify_graphql()
        if method == "GET":
            return {{"classification": "read", "operation": "github-api-read"}}
        writes = (
            ("/comments", "comment-create"),
            ("/reactions", "reaction-create"),
            ("/labels", "labels-apply"),
            ("/reviews", "review-submit"),
            ("/rerun", "check-rerun"),
            ("/merge", "merge"),
        )
        matches = [name for suffix, name in writes if suffix in endpoint]
        return {{"classification": "write", "operation": matches[0]}} if len(matches) == 1 else {{"classification": "unknown", "operation": "github-rest-unknown"}}
    if "pr" in arguments:
        operation = arguments[arguments.index("pr") + 1]
        if operation == "view":
            return {{"classification": "read", "operation": "pr-view"}}
        if operation in {{"create", "edit", "ready"}}:
            return {{"classification": "write", "operation": "pr-" + operation}}
    return {{"classification": "unknown", "operation": "github-command-unknown"}}

state["effects"].append(normalized_effect())

if "api" in arguments:
    print(json.dumps([[rest(item) for item in state["prs"] if item["state"] == "OPEN"]], sort_keys=True))
elif "pr" not in arguments:
    write(); raise SystemExit("unsupported fake gh command")
else:
    operation = arguments[arguments.index("pr") + 1]
    apply_sequence_fault(operation)
    if operation == "view":
        if not state.get("fault_applied") and state.get("fault") in {{
            "concurrent-body-after-generation",
            "restack-after-generation",
            "restack-before-canonical-write",
        }}:
            item = pr(int(arguments[arguments.index("view") + 1]))
            if state["fault"] == "concurrent-body-after-generation":
                item["body"] = "concurrent body"
            else:
                item["headRefOid"] = "c" * 40
            state["fault_applied"] = True
        if state.get("fault") == "post-write-body-drift" and state.get("edit_done"):
            pr(int(arguments[arguments.index("view") + 1]))["body"] = "post-write drift"
        print(json.dumps(pr(int(arguments[arguments.index("view") + 1])), sort_keys=True))
    elif operation == "create":
        number = state["next_number"]
        state["next_number"] += 1
        head = value("--head")
        owner, branch = head.split(":", 1)
        item = {{
            "number": number, "url": f"https://github.com/acme/app/pull/{{number}}",
            "title": value("--title"), "body": Path(value("--body-file")).read_text(encoding="utf-8"),
            "baseRefName": value("--base"), "baseRefOid": identity["base_oid"],
            "headRefName": branch, "headRefOid": identity["head_oid"],
            "headRepositoryOwner": {{"login": owner}}, "headRepository": {{"nameWithOwner": owner + "/app-fork"}},
            "isDraft": True, "state": "OPEN",
        }}
        state.setdefault("create_bodies", []).append(item["body"])
        state["prs"].append(item)
        if state.get("fault") == "create-then-timeout":
            write(); raise SystemExit(124)
        print(item["url"])
    elif operation == "edit":
        item = pr(int(arguments[arguments.index("edit") + 1]))
        if state.get("fault") == "concurrent-body-before-write":
            state.setdefault("limitations", []).append("body drift before gh edit is overwritten; GitHub PR text has no CAS")
        item["title"] = value("--title")
        item["body"] = Path(value("--body-file")).read_text(encoding="utf-8")
        state["edit_done"] = True
        if state.get("fault") == "edit-then-timeout":
            write(); raise SystemExit(124)
        print(json.dumps(item, sort_keys=True))
    elif operation == "ready":
        item = pr(int(arguments[arguments.index("ready") + 1]))
        item["isDraft"] = False
        if state.get("fault") == "commit-then-timeout":
            write(); raise SystemExit(124)
        print(json.dumps(item, sort_keys=True))
    else:
        write(); raise SystemExit("unsupported fake gh pr operation")
write()
"""
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def write_fake_gt(path: Path) -> None:
    """Write a trace-only Graphite fake with explicit read and submit traces."""
    source = f"""#!{sys.executable}
import json
import os
import sys
from pathlib import Path

state_path = Path(os.environ["PHASE7_GRAPHITE_STATE"])
state = json.loads(state_path.read_text(encoding="utf-8"))
arguments = sys.argv[1:]
state.setdefault("calls", []).append(arguments)
state.setdefault("effects", []).append(
    {{"classification": "read", "operation": "graphite-log"}}
    if arguments[:2] == ["log", "short"]
    else {{"classification": "write", "operation": "graphite-submit"}}
    if arguments == ["submit", "--stack", "--draft"]
    else {{"classification": "unknown", "operation": "graphite-unknown"}}
)
state_path.write_text(json.dumps(state, sort_keys=True) + "\\n", encoding="utf-8")
if arguments[:2] == ["log", "short"]:
    print(json.dumps({{"inspection_only": True, "arguments": arguments}}))
    raise SystemExit(0)
if arguments == ["submit", "--stack", "--draft"]:
    print(json.dumps({{"submit_trace_only": True, "arguments": arguments, "graph_mutation_proven": False}}))
    raise SystemExit(0)
raise SystemExit("fake Graphite only supports declared trace scenarios")
"""
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def run_command(
    arguments: list[str],
    *,
    home: Path,
    environment: dict[str, str],
    allowed_scripts: tuple[Path, ...] = (),
    stdin: str | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run only absolute Python or fixture-binary paths in an isolated home."""
    fixture_bin = home / "bin"
    if arguments[0] not in {
        sys.executable,
        str(fixture_bin / "gh"),
        str(fixture_bin / "gt"),
    }:
        raise ControlPlaneError("control-plane harness refuses an untrusted executable")
    protected = {
        "HOME",
        "CODEX_HOME",
        "PATH",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONNOUSERSITE",
    }
    if protected & environment.keys():
        raise ControlPlaneError(
            "control-plane harness refuses protected environment overrides"
        )
    if arguments[0] == sys.executable:
        trusted_scripts = {path.resolve() for path in allowed_scripts}
        if len(arguments) < 2 or Path(arguments[1]).resolve() not in trusted_scripts:
            raise ControlPlaneError(
                "control-plane harness refuses an unowned Python script"
            )
    return subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        check=False,
        env={
            "HOME": str(home),
            "CODEX_HOME": str(home / ".codex"),
            "PATH": str(fixture_bin),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            **environment,
        },
        input=stdin,
        cwd=cwd,
    )


def evidence(
    *,
    route: ContractRoute,
    candidate_inputs: tuple[Path, ...],
    github_state: Path,
    processes: tuple[subprocess.CompletedProcess[str], ...],
    graphite_state: Path | None = None,
    arguments: list[str] | None = None,
    initial_github_state: dict[str, object] | None = None,
    expected_final: dict[str, object] | None = None,
    expected_finals: tuple[dict[str, object], ...] | None = None,
    required_operations: tuple[str, ...] = (),
    isolated_environment: dict[str, str] | None = None,
    validator_result: subprocess.CompletedProcess[str] | None = None,
    mutable_bindings: dict[str, Any] | None = None,
    receipt_type: str | None = None,
    publication_receipts: tuple[Path, ...] = (),
) -> dict[str, Any]:
    """Bind mutable artifacts and derive, rather than accept, terminal state."""
    github = json.loads(github_state.read_text(encoding="utf-8"))
    calls = github.get("calls", [])
    effects = github.get("effects", [])
    operations = tuple(
        next(
            (
                name
                for name in ("api", "view", "create", "edit", "ready")
                if name in call
            ),
            "other",
        )
        for call in calls
    )
    processes_ok = all(item.returncode == 0 for item in processes)
    validator_ok = validator_result is None or validator_result.returncode == 0
    if expected_final is not None and expected_finals is not None:
        raise ControlPlaneError("receipt cannot mix singular and plural final state")
    expected_states = (
        expected_finals
        if expected_finals is not None
        else (expected_final,)
        if expected_final is not None
        else ()
    )
    final_matches = bool(expected_states) and all(
        sum(
            all(item.get(key) == value for key, value in expected.items())
            for item in github.get("prs", [])
        )
        == 1
        for expected in expected_states
    )
    sequence_ok = not required_operations or operations == required_operations
    normalized_effects = list(effects)
    graphite = None
    if graphite_state is not None:
        graphite = json.loads(graphite_state.read_text(encoding="utf-8"))
        normalized_effects.extend(graphite.get("effects", []))
    effects_known = all(
        isinstance(effect, dict)
        and effect.get("classification") in {"read", "write"}
        and isinstance(effect.get("operation"), str)
        for effect in normalized_effects
    )
    mutations = [
        effect
        for effect in normalized_effects
        if effect.get("classification") == "write"
    ]
    receipt_summaries: list[dict[str, Any]] = []
    receipt_matches = len(publication_receipts) == len(expected_states) > 0
    if receipt_matches:
        for receipt_path, expected in zip(publication_receipts, expected_states):
            try:
                raw = receipt_path.read_bytes()
                receipt = json.loads(raw)
                unsigned = dict(receipt)
                supplied = unsigned.pop("content_sha256")
                identity = receipt["identity"]
                final = receipt["final_state"]
                receipt_matches = receipt_matches and (
                    raw == canonical_bytes(receipt) + b"\n"
                    and supplied
                    == hashlib.sha256(canonical_bytes(unsigned)).hexdigest()
                    and receipt.get("provenance") == "canonical"
                    and receipt.get("operation")
                    in {"create", "update-text", "mark-ready"}
                    and identity.get("pr_number") == expected.get("number")
                    and (
                        "baseRefOid" not in expected
                        or identity.get("base_oid") == expected["baseRefOid"]
                    )
                    and (
                        "headRefOid" not in expected
                        or identity.get("head_oid") == expected["headRefOid"]
                    )
                    and final.get("title_sha256")
                    == hashlib.sha256(expected["title"].encode()).hexdigest()
                    and final.get("body_sha256")
                    == hashlib.sha256(expected["body"].encode()).hexdigest()
                    and final.get("is_draft") is expected["isDraft"]
                    and final.get("state") == "OPEN"
                )
                receipt_summaries.append(
                    {
                        key: receipt.get(key)
                        for key in (
                            "sequence",
                            "receipt_id",
                            "content_sha256",
                            "provenance",
                        )
                    }
                )
            except (OSError, KeyError, TypeError, json.JSONDecodeError):
                receipt_matches = False
    inferred_type = "read-only" if route.mode == "read-only" else "component-write"
    receipt_type = inferred_type if receipt_type is None else receipt_type
    if receipt_type not in {"read-only", "component-write", "integrated-write"}:
        raise ControlPlaneError("receipt type is unsupported")
    if route.mode == "read-only":
        terminal = (
            "read-only"
            if not mutations and effects_known and processes_ok and validator_ok
            else "failed-or-ambiguous"
        )
    elif route.mode == "write" and receipt_type == "integrated-write":
        terminal = (
            "verified"
            if mutations
            and processes
            and processes_ok
            and validator_result is not None
            and validator_ok
            and final_matches
            and sequence_ok
            and effects_known
            and receipt_matches
            else "failed-or-ambiguous"
        )
    elif route.mode == "write" and receipt_type == "component-write":
        terminal = (
            "component-verified"
            if mutations
            and effects_known
            and processes_ok
            and final_matches
            and sequence_ok
            else "failed-or-ambiguous"
        )
    else:
        terminal = "unsupported-route-mode"
    result: dict[str, Any] = {
        "contract_routing": {
            "intent": route.intent,
            "owner": route.owner,
            "mode": route.mode,
            "proof": "declared contract only; not natural-language or model dispatch",
        },
        "candidate_inputs": {str(path): file_digest(path) for path in candidate_inputs},
        "github_state_sha256": digest(github),
        "github_calls": calls,
        "operations": operations,
        "semantic_effects": normalized_effects,
        "processes": [
            {
                "returncode": item.returncode,
                "stdout": item.stdout,
                "stderr": item.stderr,
            }
            for item in processes
        ],
        "receipt_type": receipt_type,
        "expected_finals": expected_states,
        "environment": {"python": sys.version, "platform": platform.platform()},
        "terminal": terminal,
    }
    if arguments is not None:
        result["argv"] = arguments
    if initial_github_state is not None:
        result["initial_github_state_sha256"] = digest(initial_github_state)
    if isolated_environment is not None:
        result["isolated_environment"] = isolated_environment
    if validator_result is not None:
        result["validator_result"] = {
            "returncode": validator_result.returncode,
            "stdout": validator_result.stdout,
            "stderr": validator_result.stderr,
        }
    if publication_receipts:
        result["publication_receipts"] = receipt_summaries
    if mutable_bindings is not None:
        result["mutable_bindings_sha256"] = digest(mutable_bindings)
    if graphite_state is not None:
        assert graphite is not None
        result["graphite_trace"] = graphite.get("calls", [])
        result["graphite_state_sha256"] = digest(graphite)
        result["graphite_proof"] = "trace protocol only; not Graphite mutation proof"
    return result


def evidence_is_current(
    receipt: dict[str, Any],
    candidate_inputs: tuple[Path, ...],
    github_state: Path,
    mutable_bindings: dict[str, Any] | None = None,
    isolated_environment: dict[str, str] | None = None,
) -> bool:
    """Reject drift in every mutable candidate artifact and simulated state."""
    recorded = receipt.get("candidate_inputs")
    if not isinstance(recorded, dict):
        return False
    try:
        current = {str(path): file_digest(path) for path in candidate_inputs}
        state = json.loads(github_state.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    expected_environment = {"python": sys.version, "platform": platform.platform()}
    recorded_bindings = receipt.get("mutable_bindings_sha256")
    recorded_isolation = receipt.get("isolated_environment")
    return (
        recorded == current
        and receipt.get("github_state_sha256") == digest(state)
        and receipt.get("environment") == expected_environment
        and (recorded_bindings is None or mutable_bindings is not None)
        and (recorded_bindings is None or recorded_bindings == digest(mutable_bindings))
        and (recorded_isolation is None or isolated_environment is not None)
        and (recorded_isolation is None or recorded_isolation == isolated_environment)
    )
