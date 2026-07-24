from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "scripts/phase7_control_plane.py"
PUBLISHER = (
    ROOT
    / "plugins/mergecraft/skills/publishing-reviewable-prs/scripts/update_reviewable_pr.py"
)
CREATOR = (
    ROOT
    / "plugins/mergecraft/skills/publishing-reviewable-prs/scripts/create_reviewable_pr.py"
)
VALIDATOR = (
    ROOT
    / "plugins/mergecraft/skills/writing-reviewable-pr-descriptions/scripts/validate_change_navigation.py"
)
STATE_HELPER = (
    ROOT
    / "plugins/mergecraft/skills/publishing-reviewable-prs/scripts/reviewable_pr_state.py"
)
SPEC = importlib.util.spec_from_file_location("phase7_control_plane", RUNNER_PATH)
assert SPEC and SPEC.loader
CONTROL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CONTROL
SPEC.loader.exec_module(CONTROL)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


BODY = CONTROL.render_writer_fixture(2)
BASE_OID = "a" * 40
HEAD_OID = "b" * 40
DRIFT_HEAD_OID = "c" * 40
GIT_REPOSITORY: Path | None = None


def review_input(
    title: str,
    body: str,
    *,
    baseline_body: str,
    pr_number: int = 2,
    base: str = "main",
    head: str = "acme:widget",
    head_oid: str | None = None,
) -> dict[str, object]:
    if GIT_REPOSITORY is None:
        raise RuntimeError("Phase 7 Git fixture is not initialized")
    head_oid = head_oid or HEAD_OID
    git_diff = CONTROL.observe_git_diff(
        GIT_REPOSITORY, base_oid=BASE_OID, head_oid=head_oid
    )
    raw: dict[str, object] = {
        "version": 3,
        "repository": "acme/app",
        "pr_number": pr_number,
        "base": {"ref": base, "oid": BASE_OID},
        "head": {
            "ref": head,
            "oid": head_oid,
            "owner": "acme",
            "repository": "acme/app-fork",
        },
        "candidate": {"title": title, "body_sha256": sha(body)},
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
            "mode": "existing",
            "title_sha256": sha(title),
            "body_sha256": sha(baseline_body),
            "fragments": [
                {
                    "id": "body",
                    "text": baseline_body,
                    "sha256": sha(baseline_body),
                    "disposition": "replace" if baseline_body != body else "retain",
                    "replacement": body if baseline_body != body else None,
                    "reason": "Phase 7 fixture body refresh"
                    if baseline_body != body
                    else None,
                }
            ],
        },
    }
    raw["content_sha256"] = sha(
        json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )
    return raw


def new_review_input(title: str, template: str) -> dict[str, object]:
    raw = review_input(title, template, baseline_body=template)
    raw["pr_number"] = "__PUBLISHING_REVIEWABLE_PRS_PR_NUMBER__"
    raw["candidate"] = {"title": title, "body_sha256": sha(template)}
    raw["baseline"] = {
        "mode": "new",
        "title_sha256": None,
        "body_sha256": None,
        "fragments": [],
    }
    raw["content_sha256"] = sha(
        json.dumps(
            {key: value for key, value in raw.items() if key != "content_sha256"},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    )
    return raw


def stored(
    *,
    title: str,
    body: str,
    pr_number: int = 2,
    base: str = "main",
    head: str = "widget",
    draft: bool = True,
    head_oid: str | None = None,
):
    head_oid = head_oid or HEAD_OID
    return {
        "number": pr_number,
        "url": f"https://github.com/acme/app/pull/{pr_number}",
        "title": title,
        "body": body,
        "baseRefName": base,
        "baseRefOid": BASE_OID,
        "headRefName": head,
        "headRefOid": head_oid,
        "headRepositoryOwner": {"login": "acme"},
        "headRepository": {"nameWithOwner": "acme/app-fork"},
        "isDraft": draft,
        "state": "OPEN",
    }


class Phase7ControlPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        global BASE_OID, DRIFT_HEAD_OID, HEAD_OID, GIT_REPOSITORY
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.home / "bin"
        self.bin.mkdir()
        git = shutil.which("git")
        assert git is not None
        (self.bin / "git").symlink_to(git)
        self.github = self.root / "github.json"
        self.graphite = self.root / "graphite.json"
        self.git_repository = self.root / "git-repository"
        self.git_repository.mkdir()
        for arguments in (
            ("init", "-q"),
            ("config", "user.email", "fixture@example.com"),
            ("config", "user.name", "Fixture"),
        ):
            subprocess.run(
                ["git", "-C", str(self.git_repository), *arguments], check=True
            )
        source = self.git_repository / "src/widget.ts"
        source.parent.mkdir()
        source.write_text("old one\nold two\nold three\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.git_repository), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(self.git_repository), "commit", "-qm", "base"],
            check=True,
        )
        BASE_OID = subprocess.run(
            ["git", "-C", str(self.git_repository), "rev-parse", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        source.write_text(
            "".join(f"new {index}\n" for index in range(1, 10)), encoding="utf-8"
        )
        subprocess.run(["git", "-C", str(self.git_repository), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(self.git_repository), "commit", "-qm", "head"],
            check=True,
        )
        HEAD_OID = subprocess.run(
            ["git", "-C", str(self.git_repository), "rev-parse", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        subprocess.run(
            [
                "git",
                "-C",
                str(self.git_repository),
                "commit",
                "--allow-empty",
                "-qm",
                "restacked",
            ],
            check=True,
        )
        DRIFT_HEAD_OID = subprocess.run(
            ["git", "-C", str(self.git_repository), "rev-parse", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        GIT_REPOSITORY = self.git_repository
        CONTROL.write_fake_gh(self.bin / "gh")
        CONTROL.write_fake_gt(self.bin / "gt")

    def state(
        self,
        pr: dict[str, object] | list[dict[str, object]] | None = None,
        *,
        fault: str | None = None,
    ) -> None:
        value: dict[str, object] = {
            "prs": [] if pr is None else pr if isinstance(pr, list) else [pr],
            "calls": [],
            "identity": {"base_oid": BASE_OID, "head_oid": HEAD_OID},
        }
        if fault:
            value["fault"] = fault
        self.github.write_text(json.dumps(value), encoding="utf-8")

    def command(
        self,
        *,
        title: str,
        body: str,
        expected_body: str,
        operation: str = "text",
        pr_number: int = 2,
        base: str = "main",
        head: str = "acme:widget",
        head_oid: str | None = None,
        expected_state: str = "draft",
    ):
        head_oid = head_oid or HEAD_OID
        body_path = self.root / "candidate.md"
        body_path.write_text(body, encoding="utf-8")
        input_path = self.root / "review-input.json"
        input_path.write_text(
            json.dumps(
                review_input(
                    title,
                    body,
                    baseline_body=expected_body,
                    pr_number=pr_number,
                    base=base,
                    head=head,
                    head_oid=head_oid,
                )
            ),
            encoding="utf-8",
        )
        common = [
            "--repository",
            "acme/app",
            "--pr",
            str(pr_number),
            "--base",
            base,
            "--base-oid",
            BASE_OID,
            "--head",
            head,
            "--head-oid",
            head_oid,
            "--head-owner",
            "acme",
            "--head-repository",
            "acme/app-fork",
            "--expected-title-sha256",
            sha(title),
            "--expected-body-sha256",
            sha(expected_body),
            "--review-input",
            str(input_path),
        ]
        if operation == "ready":
            arguments = [sys.executable, str(PUBLISHER), "ready", *common]
        else:
            arguments = [
                sys.executable,
                str(PUBLISHER),
                "text",
                *common,
                "--expected-state",
                expected_state,
                "--text-scope",
                "body-only",
                "--title",
                title,
                "--body-file",
                str(body_path),
            ]
        environment = {"PHASE7_GITHUB_STATE": str(self.github)}
        return (
            CONTROL.run_command(
                arguments,
                home=self.home,
                environment=environment,
                allowed_scripts=(PUBLISHER,),
                cwd=self.git_repository,
            ),
            input_path,
            body_path,
        )

    def create_command(self, *, title: str):
        template, input_path = CONTROL.write_new_draft_fixture(
            self.root,
            title=title,
            git_repository=self.git_repository,
            base_oid=BASE_OID,
            head_oid=HEAD_OID,
        )
        capture = CONTROL.capture_new_draft_command(
            publisher=CREATOR,
            repository="acme/app",
            base="main",
            base_oid=BASE_OID,
            head="acme:widget",
            head_oid=HEAD_OID,
            head_owner="acme",
            head_repository="acme/app-fork",
            title=title,
            body_template=template,
            review_input=input_path,
        )
        validation_body = self.root / "validator-body.md"
        validation_body.write_text(
            template.read_text(encoding="utf-8").replace(
                "__PUBLISHING_REVIEWABLE_PRS_PR_NUMBER__", "2147483647"
            ),
            encoding="utf-8",
        )
        validator = CONTROL.run_command(
            [
                sys.executable,
                str(VALIDATOR),
                str(validation_body),
                "--template-body",
                str(template),
                "--repository",
                "acme/app",
                "--pr",
                "2147483647",
                "--title",
                title,
                "--review-input",
                str(input_path),
            ],
            home=self.home,
            environment={},
            allowed_scripts=(VALIDATOR,),
            cwd=self.git_repository,
        )
        return (
            CONTROL.run_command(
                list(capture.arguments),
                home=self.home,
                environment={"PHASE7_GITHUB_STATE": str(self.github)},
                allowed_scripts=(CREATOR,),
                cwd=self.git_repository,
            ),
            capture,
            input_path,
            template,
            validator,
        )

    def route(self, intent: str, owner: str, mode: str):
        return CONTROL.resolve_contract_route(
            intent, (CONTROL.ContractRoute(intent, owner, mode),)
        )

    def test_existing_draft_update_runs_actual_publisher_against_fake_gh(self) -> None:
        old_body = BODY.replace("9 additions", "8 additions")
        self.state(stored(title="feat: widget", body=old_body))
        result, review_input_path, body_path = self.command(
            title="feat: widget", body=BODY, expected_body=old_body
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.github.read_text())
        self.assertEqual(state["prs"][0]["body"], BODY)
        self.assertEqual(
            [call[2:4] for call in state["calls"]],
            [
                ["pr", "view"],
                ["pr", "view"],
                ["pr", "edit"],
                ["pr", "view"],
            ],
        )
        report = CONTROL.evidence(
            route=self.route(
                "update this draft", "mergecraft:publishing-reviewable-prs", "write"
            ),
            candidate_inputs=(PUBLISHER, review_input_path, body_path),
            github_state=self.github,
            processes=(result,),
            expected_final={
                "number": 2,
                "title": "feat: widget",
                "body": BODY,
                "isDraft": True,
            },
            required_operations=("view", "view", "edit", "view"),
        )
        self.assertEqual(report["terminal"], "component-verified")
        self.assertIn("candidate_inputs", report)

    def test_existing_ready_text_update_uses_one_edit_and_final_reread(self) -> None:
        old_body = BODY.replace("9 additions", "8 additions")
        self.state(stored(title="feat: widget", body=old_body, draft=False))
        result, _, _ = self.command(
            title="feat: widget",
            body=BODY,
            expected_body=old_body,
            expected_state="ready",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = json.loads(self.github.read_text())["calls"]
        self.assertEqual(
            [call[2:4] for call in calls],
            [
                ["pr", "view"],
                ["pr", "view"],
                ["pr", "edit"],
                ["pr", "view"],
            ],
        )

    def test_new_draft_uses_captured_argv_and_recovers_a_nonce_tagged_create(
        self,
    ) -> None:
        self.state(fault="create-then-timeout")
        initial_state = json.loads(self.github.read_text())
        result, capture, review_input_path, template, validator = self.create_command(
            title="feat: widget"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(capture.arguments)[1], str(CREATOR))
        state = json.loads(self.github.read_text())
        self.assertEqual(len(state["prs"]), 1)
        self.assertTrue(state["prs"][0]["isDraft"])
        self.assertEqual(state["prs"][0]["body"], CONTROL.render_writer_fixture(2))
        self.assertEqual(len(state["create_bodies"]), 1)
        self.assertIn("transaction=", state["create_bodies"][0])
        self.assertTrue(any("api" in call for call in state["calls"]))
        self.assertEqual(sum("edit" in call for call in state["calls"]), 1)
        self.assertEqual(state["calls"][-1][2:4], ["pr", "view"])
        report = CONTROL.evidence(
            route=self.route(
                "create this draft", "mergecraft:publishing-reviewable-prs", "write"
            ),
            candidate_inputs=(
                ROOT / "scripts/phase7_control_plane.py",
                CREATOR,
                STATE_HELPER,
                *CONTROL.writer_validator_inputs(VALIDATOR),
                review_input_path,
                template,
                self.bin / "gh",
                self.bin / "gt",
            ),
            github_state=self.github,
            processes=(result,),
            arguments=list(capture.arguments),
            initial_github_state=initial_state,
            expected_final={
                "number": 2,
                "title": "feat: widget",
                "body": CONTROL.render_writer_fixture(2),
                "isDraft": True,
                "baseRefOid": BASE_OID,
                "headRefOid": HEAD_OID,
            },
            required_operations=(
                "api",
                "create",
                "api",
                "view",
                "view",
                "edit",
                "view",
            ),
            isolated_environment={
                "HOME": str(self.home),
                "CODEX_HOME": str(self.home / ".codex"),
                "PATH": str(self.bin),
            },
            validator_result=validator,
            mutable_bindings={
                "HOME": str(self.home),
                "CODEX_HOME": str(self.home / ".codex"),
                "PATH": str(self.bin),
                "fake_tools": {
                    "gh": CONTROL.file_digest(self.bin / "gh"),
                    "gt": CONTROL.file_digest(self.bin / "gt"),
                },
            },
        )
        self.assertEqual(report["terminal"], "component-verified")
        self.assertEqual(report["argv"], list(capture.arguments))
        self.assertIn("initial_github_state_sha256", report)

    def test_failed_process_cannot_receive_a_verified_receipt(self) -> None:
        self.state(stored(title="feat: widget", body=BODY))
        report = CONTROL.evidence(
            route=self.route(
                "update this draft", "mergecraft:publishing-reviewable-prs", "write"
            ),
            candidate_inputs=(PUBLISHER,),
            github_state=self.github,
            processes=(__import__("subprocess").CompletedProcess([], 1, "", "failed"),),
            expected_final={
                "number": 2,
                "title": "feat: widget",
                "body": BODY,
                "isDraft": True,
            },
            required_operations=(),
        )
        self.assertEqual(report["terminal"], "failed-or-ambiguous")

    def test_ready_timeout_never_mints_canonical_provenance(self) -> None:
        self.state(stored(title="feat: widget", body=BODY), fault="commit-then-timeout")
        result, _, _ = self.command(
            title="feat: widget", body=BODY, expected_body=BODY, operation="ready"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot prove causality", result.stderr)
        state = json.loads(self.github.read_text())
        self.assertFalse(state["prs"][0]["isDraft"])
        self.assertEqual(
            [call[2:4] for call in state["calls"]],
            [["pr", "view"], ["pr", "view"], ["pr", "ready"], ["pr", "view"]],
        )
        receipt_root = self.home / ".local/state/mergecraft/pr-publication-receipts"
        self.assertFalse(list(receipt_root.rglob("*.json")))

    def test_body_and_restack_drift_block_before_any_mutation(self) -> None:
        for name, pr, expected_error in (
            (
                "concurrent-body",
                stored(title="feat: widget", body="concurrent body"),
                "preimage changed",
            ),
            (
                "restack-head",
                stored(title="feat: widget", body=BODY, head_oid="c" * 40),
                "base/head changed",
            ),
        ):
            with self.subTest(name=name):
                self.state(pr)
                result, _, _ = self.command(
                    title="feat: widget", body=BODY, expected_body=BODY
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected_error, result.stderr)
                calls = json.loads(self.github.read_text())["calls"]
                self.assertEqual([call[2:4] for call in calls], [["pr", "view"]])

    def test_sequence_faults_after_generation_are_classified_by_preflight_or_final_read(
        self,
    ) -> None:
        for name, fault, expected_error in (
            ("concurrent-body", "concurrent-body-after-generation", "preimage changed"),
            ("restack", "restack-after-generation", "base/head changed"),
        ):
            with self.subTest(name=name):
                self.state(stored(title="feat: widget", body=BODY), fault=fault)
                result, _, _ = self.command(
                    title="feat: widget", body=BODY, expected_body=BODY
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected_error, result.stderr)
                calls = json.loads(self.github.read_text())["calls"]
                self.assertEqual([call[2:4] for call in calls], [["pr", "view"]])

    def test_write_sequence_races_are_finally_reread_and_never_retried(self) -> None:
        old_body = BODY.replace("9 additions", "8 additions")
        self.state(
            stored(title="feat: widget", body=old_body),
            fault="concurrent-body-before-write",
        )
        result, _, _ = self.command(
            title="feat: widget", body=BODY, expected_body=old_body
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.github.read_text())
        self.assertIn("GitHub PR text has no CAS", state["limitations"][0])
        self.assertEqual(
            [call[2:4] for call in state["calls"]],
            [
                ["pr", "view"],
                ["pr", "view"],
                ["pr", "edit"],
                ["pr", "view"],
            ],
        )

        self.state(
            stored(title="feat: widget", body=old_body), fault="restack-before-write"
        )
        result, _, _ = self.command(
            title="feat: widget", body=BODY, expected_body=old_body
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no retry or rollback", result.stderr)
        calls = json.loads(self.github.read_text())["calls"]
        self.assertEqual(
            [call[2:4] for call in calls],
            [
                ["pr", "view"],
                ["pr", "view"],
                ["pr", "edit"],
                ["pr", "view"],
            ],
        )

    def test_post_write_drift_is_observed_at_the_final_reread(self) -> None:
        old_body = BODY.replace("9 additions", "8 additions")
        self.state(
            stored(title="feat: widget", body=old_body),
            fault="post-write-body-drift",
        )
        result, _, _ = self.command(
            title="feat: widget", body=BODY, expected_body=old_body
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no retry or rollback", result.stderr)

    def test_restack_requires_a_fresh_refetch_then_allows_one_new_write(self) -> None:
        old_body = BODY.replace("9 additions", "8 additions")
        self.state(stored(title="feat: widget", body=old_body, head_oid=DRIFT_HEAD_OID))
        stale, _, _ = self.command(
            title="feat: widget", body=BODY, expected_body=old_body
        )
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn("base/head changed", stale.stderr)
        refreshed, _, _ = self.command(
            title="feat: widget",
            body=BODY,
            expected_body=old_body,
            head_oid=DRIFT_HEAD_OID,
        )
        self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
        calls = json.loads(self.github.read_text())["calls"]
        self.assertEqual(
            [call[2:4] for call in calls],
            [
                ["pr", "view"],
                ["pr", "view"],
                ["pr", "view"],
                ["pr", "edit"],
                ["pr", "view"],
            ],
        )

    def test_create_rechecks_restack_before_the_canonical_edit(self) -> None:
        self.state(fault="restack-before-canonical-write")
        result, _, _, _, _ = self.create_command(title="feat: widget")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no automatic retry or rollback", result.stderr)
        calls = json.loads(self.github.read_text())["calls"]
        self.assertFalse(any("edit" in call for call in calls))

    def test_graphite_is_trace_only_and_read_only_intent_has_no_mutation(self) -> None:
        self.graphite.write_text(json.dumps({"calls": []}), encoding="utf-8")
        route = self.route("inspect stacked PR", "mergecraft:graphite", "read-only")
        result = CONTROL.run_command(
            [str(self.bin / "gt"), "log", "short"],
            home=self.home,
            environment={"PHASE7_GRAPHITE_STATE": str(self.graphite)},
        )
        self.assertEqual(result.returncode, 0)
        self.state(stored(title="feat: widget", body=BODY))
        report = CONTROL.evidence(
            route=route,
            candidate_inputs=(PUBLISHER,),
            github_state=self.github,
            graphite_state=self.graphite,
            processes=(result,),
        )
        self.assertEqual(report["github_calls"], [])
        self.assertEqual(report["graphite_trace"], [["log", "short"]])
        self.assertIn("not Graphite mutation", report["graphite_proof"])

    def test_stacked_submit_trace_is_repaired_by_one_canonical_edit_per_pr(
        self,
    ) -> None:
        first_old_body = CONTROL.render_writer_fixture(2).replace(
            "9 additions", "8 additions"
        )
        second_old_body = CONTROL.render_writer_fixture(3).replace(
            "9 additions", "8 additions"
        )
        self.state(
            [
                stored(
                    title="feat: layer one",
                    body=first_old_body,
                    pr_number=2,
                    head="layer-one",
                ),
                stored(
                    title="feat: layer two",
                    body=second_old_body,
                    pr_number=3,
                    base="layer-one",
                    head="layer-two",
                ),
            ]
        )
        self.graphite.write_text(json.dumps({"calls": []}), encoding="utf-8")
        submit = CONTROL.run_command(
            [str(self.bin / "gt"), "submit", "--stack", "--draft"],
            home=self.home,
            environment={"PHASE7_GRAPHITE_STATE": str(self.graphite)},
        )
        self.assertEqual(submit.returncode, 0, submit.stderr)
        first, first_input, first_body = self.command(
            title="feat: layer one",
            body=CONTROL.render_writer_fixture(2),
            expected_body=first_old_body,
            pr_number=2,
            head="acme:layer-one",
        )
        second, second_input, second_body = self.command(
            title="feat: layer two",
            body=CONTROL.render_writer_fixture(3),
            expected_body=second_old_body,
            pr_number=3,
            base="layer-one",
            head="acme:layer-two",
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        state = json.loads(self.github.read_text())
        self.assertEqual(
            [
                (
                    item["number"],
                    item["baseRefName"],
                    item["headRefName"],
                    item["title"],
                    item["body"],
                    item["isDraft"],
                )
                for item in state["prs"]
            ],
            [
                (
                    2,
                    "main",
                    "layer-one",
                    "feat: layer one",
                    CONTROL.render_writer_fixture(2),
                    True,
                ),
                (
                    3,
                    "layer-one",
                    "layer-two",
                    "feat: layer two",
                    CONTROL.render_writer_fixture(3),
                    True,
                ),
            ],
        )
        self.assertEqual(
            [call[2:4] for call in state["calls"]],
            [
                ["pr", "view"],
                ["pr", "view"],
                ["pr", "edit"],
                ["pr", "view"],
            ]
            * 2,
        )
        report = CONTROL.evidence(
            route=self.route("submit this draft stack", "mergecraft:graphite", "write"),
            candidate_inputs=(
                RUNNER_PATH,
                PUBLISHER,
                STATE_HELPER,
                *CONTROL.writer_validator_inputs(VALIDATOR),
                first_input,
                first_body,
                second_input,
                second_body,
                self.bin / "gh",
                self.bin / "gt",
            ),
            github_state=self.github,
            graphite_state=self.graphite,
            processes=(submit, first, second),
            expected_finals=(
                {
                    "number": 2,
                    "baseRefName": "main",
                    "headRefName": "layer-one",
                    "headRefOid": HEAD_OID,
                    "title": "feat: layer one",
                    "body": CONTROL.render_writer_fixture(2),
                    "isDraft": True,
                },
                {
                    "number": 3,
                    "baseRefName": "layer-one",
                    "headRefName": "layer-two",
                    "headRefOid": HEAD_OID,
                    "title": "feat: layer two",
                    "body": CONTROL.render_writer_fixture(3),
                    "isDraft": True,
                },
            ),
            required_operations=(
                "view",
                "view",
                "edit",
                "view",
                "view",
                "view",
                "edit",
                "view",
            ),
        )
        self.assertEqual(report["terminal"], "component-verified")
        self.assertEqual(report["graphite_trace"], [["submit", "--stack", "--draft"]])
        self.assertIn("not Graphite mutation", report["graphite_proof"])

    def test_integrated_receipt_is_hook_free_and_binds_writer_final_reread_and_receipt(
        self,
    ) -> None:
        self.state()
        result, capture, review_input_path, template, validator = self.create_command(
            title="feat: widget"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        receipts = tuple(
            (self.home / ".local/state/mergecraft/pr-publication-receipts").rglob(
                "*.json"
            )
        )
        self.assertEqual(len(receipts), 1)
        expected = {
            "number": 2,
            "title": "feat: widget",
            "body": CONTROL.render_writer_fixture(2),
            "isDraft": True,
            "baseRefOid": BASE_OID,
            "headRefOid": HEAD_OID,
        }

        def integrated(**overrides):
            arguments = {
                "route": self.route(
                    "create this draft", "mergecraft:publisher", "write"
                ),
                "candidate_inputs": (CREATOR, review_input_path, template),
                "github_state": self.github,
                "processes": (result,),
                "expected_final": expected,
                "required_operations": (
                    "api",
                    "create",
                    "view",
                    "view",
                    "edit",
                    "view",
                ),
                "validator_result": validator,
                "receipt_type": "integrated-write",
                "publication_receipts": receipts,
                "arguments": list(capture.arguments),
            }
            arguments.update(overrides)
            return CONTROL.evidence(**arguments)

        self.assertEqual(integrated()["terminal"], "verified")
        self.assertEqual(
            integrated(publication_receipts=())["terminal"], "failed-or-ambiguous"
        )
        self.assertEqual(
            integrated(validator_result=None)["terminal"], "failed-or-ambiguous"
        )
        mismatched = self.root / "mismatched-receipt.json"
        mismatched.write_text(receipts[0].read_text().replace("canonical", "changed"))
        self.assertEqual(
            integrated(publication_receipts=(mismatched,))["terminal"],
            "failed-or-ambiguous",
        )
        drifted_expected = {**expected, "headRefOid": "c" * 40}
        self.assertEqual(
            integrated(expected_final=drifted_expected)["terminal"],
            "failed-or-ambiguous",
        )

    def test_read_only_proof_uses_normalized_actuator_effects_and_fails_closed(
        self,
    ) -> None:
        cases = (
            (
                "rest-get",
                ["--method", "GET", "repos/acme/app/issues/2/comments"],
                "read-only",
            ),
            (
                "graphql-query",
                [
                    "--method",
                    "POST",
                    "graphql",
                    "-f",
                    "query=query { viewer { login } }",
                ],
                "read-only",
            ),
            (
                "comment",
                ["--method", "POST", "repos/acme/app/issues/2/comments"],
                "failed-or-ambiguous",
            ),
            (
                "reaction",
                ["--method", "POST", "repos/acme/app/issues/2/reactions"],
                "failed-or-ambiguous",
            ),
            (
                "labels",
                ["--method", "POST", "repos/acme/app/issues/2/labels"],
                "failed-or-ambiguous",
            ),
            (
                "review",
                ["--method", "POST", "repos/acme/app/pulls/2/reviews"],
                "failed-or-ambiguous",
            ),
            (
                "rerun",
                ["--method", "POST", "repos/acme/app/actions/runs/2/rerun"],
                "failed-or-ambiguous",
            ),
            (
                "merge",
                ["--method", "PUT", "repos/acme/app/pulls/2/merge"],
                "failed-or-ambiguous",
            ),
            (
                "thread",
                [
                    "--method",
                    "POST",
                    "graphql",
                    "-f",
                    "query=mutation { resolveReviewThread(input:{}) { thread { id } } }",
                ],
                "failed-or-ambiguous",
            ),
            (
                "unknown",
                ["--method", "POST", "repos/acme/app/unknown"],
                "failed-or-ambiguous",
            ),
        )
        for name, suffix, terminal in cases:
            with self.subTest(name=name):
                self.state()
                result = CONTROL.run_command(
                    [str(self.bin / "gh"), "api", "--hostname", "github.com", *suffix],
                    home=self.home,
                    environment={"PHASE7_GITHUB_STATE": str(self.github)},
                )
                self.assertEqual(result.returncode, 0)
                report = CONTROL.evidence(
                    route=self.route("inspect", "github", "read-only"),
                    candidate_inputs=(RUNNER_PATH,),
                    github_state=self.github,
                    processes=(result,),
                )
                self.assertEqual(report["terminal"], terminal)

    def test_graphql_input_stdin_is_captured_and_classified_before_default_get(
        self,
    ) -> None:
        cases = (
            (
                "query",
                "query Viewer { viewer { login } }",
                "github-graphql-query",
                "read-only",
            ),
            (
                "anonymous-query",
                "{ viewer { login } }",
                "github-graphql-query",
                "read-only",
            ),
            (
                "comment",
                "mutation C { addComment(input:{}) { subject { id } } }",
                "comment-create",
                "failed-or-ambiguous",
            ),
            (
                "reply",
                "mutation R { addPullRequestReviewThreadReply(input:{}) { comment { id } } }",
                "review-reply",
                "failed-or-ambiguous",
            ),
            (
                "reaction",
                "mutation R { addReaction(input:{}) { reaction { content } } }",
                "reaction-create",
                "failed-or-ambiguous",
            ),
            (
                "labels",
                "mutation L { addLabelsToLabelable(input:{}) { clientMutationId } }",
                "labels-apply",
                "failed-or-ambiguous",
            ),
            (
                "review",
                "mutation R { addPullRequestReview(input:{}) { pullRequestReview { id } } }",
                "review-submit",
                "failed-or-ambiguous",
            ),
            (
                "thread",
                "mutation T { resolveReviewThread(input:{}) { thread { id } } }",
                "review-thread-resolution",
                "failed-or-ambiguous",
            ),
            (
                "merge",
                "mutation M { mergePullRequest(input:{}) { pullRequest { id } } }",
                "merge",
                "failed-or-ambiguous",
            ),
            (
                "unknown",
                "mutation X { deleteProjectV2(input:{}) { projectV2 { id } } }",
                "github-graphql-unknown",
                "failed-or-ambiguous",
            ),
        )
        for name, query, operation, terminal in cases:
            with self.subTest(name=name):
                self.state()
                stdin = json.dumps(
                    {"query": query, "variables": {}, "operationName": None}
                    if name == "invalid-operation-name"
                    else {"query": query, "variables": {}},
                    separators=(",", ":"),
                )
                result = CONTROL.run_command(
                    [str(self.bin / "gh"), "api", "graphql", "--input", "-"],
                    home=self.home,
                    environment={"PHASE7_GITHUB_STATE": str(self.github)},
                    stdin=stdin,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                state = json.loads(self.github.read_text())
                self.assertEqual(state["stdin"], [stdin])
                self.assertEqual(state["effects"][-1]["operation"], operation)
                report = CONTROL.evidence(
                    route=self.route("inspect", "github", "read-only"),
                    candidate_inputs=(RUNNER_PATH,),
                    github_state=self.github,
                    processes=(result,),
                )
                self.assertEqual(report["terminal"], terminal)

        for stdin in (
            "",
            "[]",
            '{"query":"query { viewer { login } }","query":"mutation { mergePullRequest(input:{}) { pullRequest { id } } }"}',
        ):
            with self.subTest(stdin=stdin):
                self.state()
                result = CONTROL.run_command(
                    [str(self.bin / "gh"), "api", "graphql", "--input", "-"],
                    home=self.home,
                    environment={"PHASE7_GITHUB_STATE": str(self.github)},
                    stdin=stdin,
                )
                state = json.loads(self.github.read_text())
                self.assertEqual(state["stdin"], [stdin])
                self.assertEqual(state["effects"][-1]["classification"], "unknown")

    def test_graphql_selects_exact_operation_for_stdin_and_field_forms(self) -> None:
        document = """
            query ReadViewer { viewer { login } }
            mutation MergeSelected {
              mergePullRequest(input:{pullRequestId:"PR"}) { pullRequest { id } }
            }
        """
        duplicate = """
            query Reused { viewer { login } }
            mutation Reused { mergePullRequest(input:{}) { pullRequest { id } } }
        """
        cases = (
            (
                "stdin-read",
                ["graphql", "--input", "-"],
                json.dumps(
                    {"query": document, "variables": {}, "operationName": "ReadViewer"}
                ),
                "read",
                "github-graphql-query",
            ),
            (
                "stdin-write",
                ["graphql", "--input", "-"],
                json.dumps(
                    {
                        "query": document,
                        "variables": {},
                        "operationName": "MergeSelected",
                    }
                ),
                "write",
                "merge",
            ),
            (
                "field-read",
                [
                    "graphql",
                    "-f",
                    f"query={document}",
                    "-f",
                    "operationName=ReadViewer",
                ],
                None,
                "read",
                "github-graphql-query",
            ),
            (
                "field-write",
                [
                    "graphql",
                    "--raw-field",
                    f"query={document}",
                    "--field",
                    "operationName=MergeSelected",
                ],
                None,
                "write",
                "merge",
            ),
            (
                "missing-selection",
                ["graphql", "--input", "-"],
                json.dumps({"query": document, "variables": {}}),
                "unknown",
                "github-graphql-unknown",
            ),
            (
                "unknown-selection",
                ["graphql", "--input", "-"],
                json.dumps({"query": document, "operationName": "Absent"}),
                "unknown",
                "github-graphql-unknown",
            ),
            (
                "duplicate-operation-name",
                ["graphql", "--input", "-"],
                json.dumps({"query": duplicate, "operationName": "Reused"}),
                "unknown",
                "github-graphql-unknown",
            ),
            (
                "duplicate-field-selection",
                [
                    "graphql",
                    "-f",
                    f"query={document}",
                    "-f",
                    "operationName=ReadViewer",
                    "-f",
                    "operationName=MergeSelected",
                ],
                None,
                "unknown",
                "github-graphql-unknown",
            ),
        )
        for name, suffix, stdin, classification, operation in cases:
            with self.subTest(name=name):
                self.state()
                result = CONTROL.run_command(
                    [str(self.bin / "gh"), "api", *suffix],
                    home=self.home,
                    environment={"PHASE7_GITHUB_STATE": str(self.github)},
                    stdin=stdin,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                effect = json.loads(self.github.read_text())["effects"][-1]
                self.assertEqual(effect["classification"], classification)
                self.assertEqual(effect["operation"], operation)

    def test_validator_import_tree_is_bound_and_receipt_rejects_recorded_drift(
        self,
    ) -> None:
        inputs = CONTROL.writer_validator_inputs(VALIDATOR)
        relative = [path.relative_to(VALIDATOR.parent).as_posix() for path in inputs]
        self.assertEqual(relative[0], "validate_change_navigation.py")
        self.assertEqual(relative[1:], sorted(relative[1:]))
        self.assertTrue(all(path.suffix == ".py" for path in inputs))
        self.state(stored(title="feat: widget", body=BODY))
        receipt = CONTROL.evidence(
            route=self.route("inspect", "mergecraft:graphite", "read-only"),
            candidate_inputs=inputs,
            github_state=self.github,
            processes=(),
        )
        altered = json.loads(json.dumps(receipt))
        altered["candidate_inputs"][str(inputs[-1])] = "sha256:changed"
        self.assertFalse(CONTROL.evidence_is_current(altered, inputs, self.github))

    def test_evidence_rejects_changed_candidate_input(self) -> None:
        fixture = self.root / "candidate-input.txt"
        fixture.write_text("frozen", encoding="utf-8")
        self.state(stored(title="feat: widget", body=BODY))
        report = CONTROL.evidence(
            route=self.route("inspect", "mergecraft:graphite", "read-only"),
            candidate_inputs=(fixture,),
            github_state=self.github,
            processes=(),
            mutable_bindings={"actuator": "sha256:bound", "PATH": str(self.bin)},
            isolated_environment={"HOME": str(self.home), "PATH": str(self.bin)},
        )
        bindings = {"actuator": "sha256:bound", "PATH": str(self.bin)}
        isolation = {"HOME": str(self.home), "PATH": str(self.bin)}
        self.assertTrue(
            CONTROL.evidence_is_current(
                report, (fixture,), self.github, bindings, isolation
            )
        )
        self.assertFalse(
            CONTROL.evidence_is_current(
                report,
                (fixture,),
                self.github,
                {"actuator": "sha256:changed", "PATH": str(self.bin)},
                isolation,
            )
        )
        self.assertFalse(
            CONTROL.evidence_is_current(
                report,
                (fixture,),
                self.github,
                bindings,
                {"HOME": str(self.home), "PATH": "/changed"},
            )
        )
        state = json.loads(self.github.read_text())
        state["calls"].append(["simulated-state-drift"])
        self.github.write_text(json.dumps(state), encoding="utf-8")
        self.assertFalse(
            CONTROL.evidence_is_current(
                report, (fixture,), self.github, bindings, isolation
            )
        )
        self.github.write_text(
            json.dumps({"prs": [stored(title="feat: widget", body=BODY)], "calls": []}),
            encoding="utf-8",
        )
        fixture.write_text("drifted", encoding="utf-8")
        self.assertFalse(
            CONTROL.evidence_is_current(
                report, (fixture,), self.github, bindings, isolation
            )
        )

    def test_runner_rejects_an_unowned_python_script(self) -> None:
        unowned = self.root / "unowned.py"
        unowned.write_text("raise SystemExit(0)\n", encoding="utf-8")
        with self.assertRaisesRegex(CONTROL.ControlPlaneError, "unowned Python"):
            CONTROL.run_command(
                [sys.executable, str(unowned)],
                home=self.home,
                environment={},
                allowed_scripts=(PUBLISHER,),
            )

    def test_runner_rejects_protected_environment_overrides(self) -> None:
        with self.assertRaisesRegex(CONTROL.ControlPlaneError, "protected environment"):
            CONTROL.run_command(
                [str(self.bin / "gt"), "log", "short"],
                home=self.home,
                environment={"PATH": "/usr/bin"},
            )


if __name__ == "__main__":
    unittest.main()
