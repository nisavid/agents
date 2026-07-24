from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[4]
SKILLS_ROOT = REPOSITORY / "plugins/mergecraft/skills"
SKILL_ROOT = SKILLS_ROOT / "getting-prs-merged"
PUBLISHER_SCRIPTS = SKILLS_ROOT / "publishing-reviewable-prs" / "scripts"
sys.path.insert(0, str(PUBLISHER_SCRIPTS))


def load_comment_module(name: str):
    path = SKILL_ROOT / "scripts" / "post_coderabbit_comment.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COMMENT = load_comment_module("post_coderabbit_comment")


class TopLevelCommentActuatorTests(unittest.TestCase):
    repository = "acme/app"
    pr_number = 42
    base = "main"
    base_oid = "a" * 40
    head = "fork-owner:widget"
    head_oid = "b" * 40
    head_owner = "fork-owner"
    head_repository = "fork-owner/app-fork"
    url = "https://github.com/acme/app/pull/42"
    login = "merge-operator"

    @property
    def expected(self):
        return COMMENT.ExpectedIdentity(
            repository=self.repository,
            pr_number=self.pr_number,
            base=self.base,
            base_oid=self.base_oid,
            head=self.head,
            head_oid=self.head_oid,
            head_owner=self.head_owner,
            head_repository=self.head_repository,
        )

    def stored(self) -> dict[str, object]:
        return {
            "number": self.pr_number,
            "url": self.url,
            "baseRefName": self.base,
            "baseRefOid": self.base_oid,
            "headRefName": "widget",
            "headRefOid": self.head_oid,
            "headRepositoryOwner": {"login": self.head_owner},
            "headRepository": {"nameWithOwner": self.head_repository},
            "title": "feat: widget",
            "body": "body",
            "isDraft": False,
            "state": "OPEN",
        }

    def test_posts_once_and_returns_exact_reread_receipt(self) -> None:
        body = "@coderabbitai review"
        created = {
            "id": 91,
            "html_url": f"{self.url}#issuecomment-91",
            "body": body,
            "user": {"login": self.login},
            "created_at": "2026-07-21T12:34:56Z",
        }
        with (
            mock.patch.object(COMMENT, "_active_login", return_value=self.login),
            mock.patch.object(
                COMMENT, "_stored_pr", return_value=self.stored()
            ) as stored,
            mock.patch.object(
                COMMENT,
                "_run_mutation",
                return_value=subprocess.CompletedProcess(
                    [], 0, json.dumps(created), ""
                ),
            ) as mutate,
            mock.patch.object(
                COMMENT,
                "_run_read",
                return_value=subprocess.CompletedProcess(
                    [], 0, json.dumps(created), ""
                ),
            ),
        ):
            receipt = COMMENT.post_comment(
                expected=self.expected,
                expected_authenticated_login=self.login,
                body=body,
                body_sha256=hashlib.sha256(body.encode()).hexdigest(),
            )
        self.assertEqual(receipt, created)
        self.assertEqual(mutate.call_count, 1)
        self.assertEqual(stored.call_count, 2)

    def test_comment_receipt_rejects_boolean_identifier(self) -> None:
        body = "@coderabbitai review"
        with self.assertRaisesRegex(COMMENT.PublicationError, "does not match"):
            COMMENT._comment_receipt(
                {
                    "id": True,
                    "html_url": f"{self.url}#issuecomment-1",
                    "body": body,
                    "user": {"login": self.login},
                    "created_at": "2026-07-21T12:34:56Z",
                },
                self.expected,
                body,
                self.login,
            )

    def test_comment_timeout_is_ambiguous_and_never_retried(self) -> None:
        body = "@coderabbitai review"
        with (
            mock.patch.object(COMMENT, "_active_login", return_value=self.login),
            mock.patch.object(COMMENT, "_stored_pr", return_value=self.stored()),
            mock.patch.object(
                COMMENT,
                "_run_mutation",
                side_effect=COMMENT.MutationAmbiguousError("timeout"),
            ) as mutate,
        ):
            with self.assertRaisesRegex(COMMENT.MutationAmbiguousError, "timeout"):
                COMMENT.post_comment(
                    expected=self.expected,
                    expected_authenticated_login=self.login,
                    body=body,
                    body_sha256=hashlib.sha256(body.encode()).hexdigest(),
                )
        self.assertEqual(mutate.call_count, 1)

    def test_rejects_wrong_or_missing_author_and_timestamp(self) -> None:
        body = "@coderabbitai review"
        valid = {
            "id": 91,
            "html_url": f"{self.url}#issuecomment-91",
            "body": body,
            "user": {"login": self.login},
            "created_at": "2026-07-21T12:34:56Z",
        }
        cases = (
            ("wrong-author", {**valid, "user": {"login": "other"}}),
            (
                "missing-author",
                {key: value for key, value in valid.items() if key != "user"},
            ),
            (
                "missing-time",
                {key: value for key, value in valid.items() if key != "created_at"},
            ),
            ("malformed-time", {**valid, "created_at": "not-a-time"}),
        )
        for name, value in cases:
            with self.subTest(name=name):
                with self.assertRaises(COMMENT.PublicationError):
                    COMMENT._comment_receipt(value, self.expected, body, self.login)

    def test_active_login_mismatch_stops_before_pr_read_or_mutation(self) -> None:
        body = "@coderabbitai review"
        with (
            mock.patch.object(COMMENT, "_active_login", return_value="other"),
            mock.patch.object(COMMENT, "_stored_pr") as stored,
            mock.patch.object(COMMENT, "_run_mutation") as mutate,
        ):
            with self.assertRaisesRegex(
                COMMENT.PublicationError, "active authenticated"
            ):
                COMMENT.post_comment(
                    expected=self.expected,
                    expected_authenticated_login=self.login,
                    body=body,
                    body_sha256=hashlib.sha256(body.encode()).hexdigest(),
                )
        stored.assert_not_called()
        mutate.assert_not_called()

    def test_concurrent_head_drift_after_comment_is_ambiguous_without_retry(
        self,
    ) -> None:
        body = "@coderabbitai review"
        receipt = {
            "id": 91,
            "html_url": f"{self.url}#issuecomment-91",
            "body": body,
            "user": {"login": self.login},
            "created_at": "2026-07-21T12:34:56Z",
        }
        drifted = {**self.stored(), "headRefOid": "c" * 40}
        with (
            mock.patch.object(COMMENT, "_active_login", return_value=self.login),
            mock.patch.object(
                COMMENT, "_stored_pr", side_effect=[self.stored(), drifted]
            ),
            mock.patch.object(
                COMMENT,
                "_run_mutation",
                return_value=subprocess.CompletedProcess(
                    [], 0, json.dumps(receipt), ""
                ),
            ) as mutate,
            mock.patch.object(
                COMMENT,
                "_run_read",
                return_value=subprocess.CompletedProcess(
                    [], 0, json.dumps(receipt), ""
                ),
            ),
        ):
            with self.assertRaisesRegex(COMMENT.PublicationError, "changed after"):
                COMMENT.post_comment(
                    expected=self.expected,
                    expected_authenticated_login=self.login,
                    body=body,
                    body_sha256=hashlib.sha256(body.encode()).hexdigest(),
                )
        self.assertEqual(mutate.call_count, 1)


if __name__ == "__main__":
    unittest.main()
