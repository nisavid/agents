import json
import copy
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


TEST_DIR = Path(__file__).resolve().parent
REPOSITORY = Path(__file__).resolve().parents[4]
SKILL_DIR = REPOSITORY / "plugins/mergecraft/skills/addressing-pr-review-feedback"
sys.path.insert(0, str(SKILL_DIR / "scripts"))

import review_feedback_state  # noqa: E402


FIXTURE_DIR = TEST_DIR / "fixtures"


def fixture(name):
    with (FIXTURE_DIR / name).open(encoding="utf-8") as handle:
        return json.load(handle)


class PrReviewStateTests(unittest.TestCase):
    def test_repo_selector_is_exact_owner_repo(self):
        self.assertEqual(
            review_feedback_state.split_repo("base-owner/base-repo"),
            ("base-owner", "base-repo"),
        )
        for malformed in ("owner/repo/extra", "owner/@/tmp/secret", "owner repo/x"):
            with self.subTest(malformed=malformed):
                with self.assertRaisesRegex(ValueError, "OWNER/REPO"):
                    review_feedback_state.split_repo(malformed)

    def test_graphql_uses_one_typed_json_stdin_payload(self):
        completed = mock.Mock(returncode=0, stdout='{"data": {}}', stderr="")
        variables = {
            "owner": "@/tmp/secret",
            "prNumber": 7,
            "includeThreads": True,
            "cursor": None,
        }
        with mock.patch.dict(
            os.environ,
            {"GH_HOST": "attacker.example", "GH_REPO": "attacker/repository"},
        ):
            with mock.patch.object(
                review_feedback_state.subprocess, "run", return_value=completed
            ) as run:
                review_feedback_state.run_gh_graphql("query Example", variables)

        command = run.call_args.args[0]
        self.assertEqual(
            command,
            [
                "gh",
                "api",
                "--hostname",
                review_feedback_state.GITHUB_HOST,
                "graphql",
                "--input",
                "-",
            ],
        )
        request = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(request["query"], "query Example")
        self.assertEqual(request["variables"], variables)
        self.assertNotIn("-F", command)
        self.assertNotIn("GH_HOST", run.call_args.kwargs["env"])
        self.assertNotIn("GH_REPO", run.call_args.kwargs["env"])

    def test_identity_comparison_rejects_boolean_pr_number(self):
        expected = {
            "repo": "base-owner/base-repo",
            "owner": "base-owner",
            "number": 1,
            "base_ref": "main",
            "base_oid": "base-sha",
            "head_ref": "feature",
            "head_oid": "head-sha",
        }
        actual = {**expected, "number": True}
        with self.assertRaisesRegex(
            review_feedback_state.IdentityError, "drifted from first-page identity"
        ):
            review_feedback_state.validate_matching_identity(
                expected, actual, "thread comment page"
            )

    def test_strict_graphql_json_rejects_duplicate_and_non_finite_values(self):
        with self.assertRaisesRegex(
            review_feedback_state.ResponseShapeError, "duplicate JSON key"
        ):
            review_feedback_state.strict_json('{"data": 1, "data": 2}', "GraphQL")

        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                with self.assertRaisesRegex(
                    review_feedback_state.ResponseShapeError,
                    "non-finite JSON value",
                ):
                    review_feedback_state.strict_json(
                        f'{{"data": {constant}}}', "GraphQL"
                    )

    def test_default_query_includes_top_level_comments(self):
        _, variables = review_feedback_state.build_pr_query(
            repo="base-owner/base-repo",
            pr_number=7,
            cursors={},
        )

        self.assertTrue(variables["includeComments"])

    def test_builds_query_variables_for_base_repository(self):
        query, variables = review_feedback_state.build_pr_query(
            repo="base-owner/base-repo",
            pr_number=7,
            cursors={
                "threads": None,
                "reviews": None,
                "checks": None,
                "review_requests": None,
            },
            include={
                "threads": True,
                "reviews": True,
                "checks": True,
                "review_requests": True,
            },
        )

        self.assertIn("pullRequest(number: $prNumber)", query)
        self.assertIn("nameWithOwner", query)
        self.assertEqual(
            variables,
            {
                "owner": "base-owner",
                "name": "base-repo",
                "prNumber": 7,
                "threadsCursor": None,
                "commentsCursor": None,
                "reviewsCursor": None,
                "checksCursor": None,
                "reviewRequestsCursor": None,
                "includeThreads": True,
                "includeComments": False,
                "includeReviews": True,
                "includeChecks": True,
                "includeReviewRequests": True,
            },
        )

    def test_paginated_threads_are_merged_and_block_readiness(self):
        state = review_feedback_state.state_from_pages(
            repo="base-owner/base-repo",
            pages=[fixture("paginated_page_1.json"), fixture("paginated_page_2.json")],
        )

        self.assertEqual(state["repo"]["owner"], "base-owner")
        self.assertEqual(state["pr"]["number"], 7)
        self.assertEqual(len(state["github_state"]["unresolved_threads"]), 2)
        self.assertEqual(state["github_state"]["pagination_complete"], True)
        self.assertEqual(state["next_blocker"], "unresolved_review_threads")
        self.assertNotIn("merge_ready", state)

    def test_fork_head_repository_identity_is_retained_in_snapshot(self):
        state = review_feedback_state.state_from_pages(
            repo="base-owner/base-repo",
            pages=[fixture("fork_pr.json")],
        )

        self.assertEqual(state["repo"]["head_repository"], "fork-owner/base-repo")
        self.assertEqual(state["repo"]["head_owner"], "fork-owner")

    def test_rejects_fork_head_repository_drift_across_pagination(self):
        first = fixture("fork_pr.json")
        first_pr = first["data"]["repository"]["pullRequest"]
        first_pr["comments"]["pageInfo"] = {
            "hasNextPage": True,
            "endCursor": "comment-page-1",
        }
        second = copy.deepcopy(first)
        second_pr = second["data"]["repository"]["pullRequest"]
        second_pr["comments"] = {
            "nodes": [],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }
        for connection in ("reviewThreads", "reviews", "reviewRequests"):
            second_pr[connection]["nodes"] = []
        second_pr["statusCheckRollup"]["contexts"]["nodes"] = []

        for label, mutate in (
            (
                "repository",
                lambda head: head.update({"nameWithOwner": "fork-owner/other-repo"}),
            ),
            (
                "owner",
                lambda head: head.update(
                    {
                        "nameWithOwner": "other-fork-owner/base-repo",
                        "owner": {"login": "other-fork-owner"},
                    }
                ),
            ),
        ):
            with self.subTest(label=label):
                drifting = copy.deepcopy(second)
                head_repository = drifting["data"]["repository"]["pullRequest"][
                    "headRepository"
                ]
                mutate(head_repository)
                with self.assertRaisesRegex(
                    review_feedback_state.IdentityError,
                    "drifted from first-page identity",
                ):
                    review_feedback_state.state_from_pages(
                        "base-owner/base-repo", [first, drifting]
                    )

    def test_paginated_comments_merge_by_thread_id(self):
        first_page = fixture("paginated_comments_page_1.json")
        second_page = fixture("paginated_comments_page_2.json")
        first_page["data"]["repository"]["pullRequest"]["reviewThreads"]["pageInfo"] = {
            "hasNextPage": True,
            "endCursor": "thread-page-1",
        }
        state = review_feedback_state.state_from_pages(
            repo="base-owner/base-repo",
            pages=[first_page, second_page],
        )

        threads = state["github_state"]["unresolved_threads"]
        self.assertEqual(len(threads), 1)
        self.assertEqual(threads[0]["id"], "comment-thread")
        self.assertEqual(threads[0]["latest_comment_author"], "second-reviewer")
        self.assertEqual(
            threads[0]["latest_comment_created_at"], "2026-04-28T00:02:00Z"
        )
        self.assertEqual(state["github_state"]["pagination_complete"], True)

    def test_fetch_pages_hydrates_comments_with_thread_specific_query(self):
        calls = []

        def fake_graphql(query, variables):
            calls.append((query, variables))
            if "ThreadComments" in query:
                return {
                    "data": {
                        "node": {
                            "pullRequest": {
                                "number": 7,
                                "baseRefName": "main",
                                "headRefName": "feature",
                                "baseRefOid": "base-sha",
                                "headRefOid": "head-sha",
                                "headRepository": {
                                    "nameWithOwner": "base-owner/base-repo",
                                    "owner": {"login": "base-owner"},
                                },
                                "repository": {
                                    "nameWithOwner": "base-owner/base-repo",
                                    "owner": {"login": "base-owner"},
                                },
                            },
                            "comments": {
                                "nodes": [
                                    {
                                        "author": {"login": "second-reviewer"},
                                        "body": "Second comment",
                                        "createdAt": "2026-04-28T00:02:00Z",
                                        "url": "https://github.com/base-owner/base-repo/pull/7#discussion_c2",
                                    }
                                ],
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            },
                        }
                    }
                }
            return fixture("paginated_comments_page_1.json")

        original = review_feedback_state.run_gh_graphql
        review_feedback_state.run_gh_graphql = fake_graphql
        try:
            pages = review_feedback_state.fetch_pages("base-owner/base-repo", 7)
        finally:
            review_feedback_state.run_gh_graphql = original

        first_query, first_variables = calls[0]
        self.assertIn("commentsCursor", first_query)
        self.assertIsNone(first_variables["commentsCursor"])
        self.assertIn("headRepository", calls[1][0])
        self.assertEqual(calls[1][1]["threadId"], "comment-thread")
        comments = pages[0]["data"]["repository"]["pullRequest"]["reviewThreads"][
            "nodes"
        ][0]["comments"]
        self.assertEqual(len(comments["nodes"]), 2)
        self.assertEqual(comments["pageInfo"]["hasNextPage"], False)

    def test_paginated_review_requests_and_checks_are_merged(self):
        state = review_feedback_state.state_from_pages(
            repo="base-owner/base-repo",
            pages=[
                fixture("paginated_requests_checks_page_1.json"),
                fixture("paginated_requests_checks_page_2.json"),
            ],
        )

        self.assertEqual(state["github_state"]["requested_reviewers"], ["Copilot"])
        self.assertEqual(state["github_state"]["requested_teams"], ["review-team"])
        self.assertEqual(
            [check["name"] for check in state["github_state"]["checks"]],
            ["lint", "test"],
        )
        self.assertEqual(state["next_blocker"], "requested_reviewers")

    def test_fetch_pages_only_requeries_active_top_level_connections(self):
        calls = []

        def fake_graphql(query, variables):
            calls.append((query, variables))
            if len(calls) == 1:
                return fixture("mixed_top_level_page_1.json")
            return fixture("mixed_top_level_page_2.json")

        original = review_feedback_state.run_gh_graphql
        review_feedback_state.run_gh_graphql = fake_graphql
        try:
            pages = review_feedback_state.fetch_pages("base-owner/base-repo", 7)
        finally:
            review_feedback_state.run_gh_graphql = original

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][1]["includeThreads"], False)
        self.assertEqual(calls[1][1]["includeComments"], False)
        self.assertEqual(calls[1][1]["includeReviews"], False)
        self.assertEqual(calls[1][1]["includeChecks"], False)
        self.assertEqual(calls[1][1]["includeReviewRequests"], True)
        state = review_feedback_state.state_from_pages("base-owner/base-repo", pages)
        self.assertEqual(
            state["github_state"]["requested_reviewers"], ["Copilot", "ReviewerTwo"]
        )
        self.assertEqual(
            [check["name"] for check in state["github_state"]["checks"]], ["lint"]
        )

    def test_collects_paginated_top_level_comments_and_review_bodies(self):
        first_page = fixture("clean_pr.json")
        second_page = fixture("clean_pr.json")
        first_pr = first_page["data"]["repository"]["pullRequest"]
        second_pr = second_page["data"]["repository"]["pullRequest"]
        first_pr["comments"] = {
            "nodes": [
                {
                    "author": {"login": "first-reviewer"},
                    "body": "Please clarify the rollout.",
                    "createdAt": "2026-04-28T00:00:00Z",
                    "url": "https://example.test/comments/1",
                }
            ],
            "pageInfo": {"hasNextPage": True, "endCursor": "comment-cursor"},
        }
        second_pr["comments"] = {
            "nodes": [
                {
                    "author": {"login": "second-reviewer"},
                    "body": "Thanks.",
                    "createdAt": "2026-04-28T00:01:00Z",
                    "url": "https://example.test/comments/2",
                }
            ],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }
        second_pr["reviewThreads"]["nodes"] = []
        second_pr["reviews"]["nodes"] = []
        second_pr["reviewRequests"]["nodes"] = []
        second_pr["statusCheckRollup"]["contexts"]["nodes"] = []
        first_pr["reviews"]["nodes"][0]["body"] = "Please add a test."
        first_pr["reviews"]["nodes"][0]["url"] = "https://example.test/reviews/1"

        state = review_feedback_state.state_from_pages(
            repo="base-owner/base-repo",
            pages=[first_page, second_page],
        )

        self.assertEqual(
            [comment["author"] for comment in state["github_state"]["comments"]],
            ["first-reviewer", "second-reviewer"],
        )
        self.assertEqual(
            state["github_state"]["reviews"][0]["body"], "Please add a test."
        )
        self.assertTrue(state["github_state"]["pagination_complete"])

    def test_outdated_threads_are_reported_separately(self):
        state = review_feedback_state.state_from_pages(
            repo="base-owner/base-repo",
            pages=[fixture("outdated_thread.json")],
        )

        self.assertEqual(len(state["github_state"]["unresolved_threads"]), 1)
        self.assertEqual(
            state["github_state"]["unresolved_threads"][0]["is_outdated"], True
        )
        self.assertEqual(state["next_blocker"], "outdated_unresolved_review_threads")
        self.assertNotIn("merge_ready", state)

    def test_requested_reviewers_block_readiness(self):
        state = review_feedback_state.state_from_pages(
            repo="base-owner/base-repo",
            pages=[fixture("requested_reviewer.json")],
        )

        self.assertEqual(state["github_state"]["requested_reviewers"], ["Copilot"])
        self.assertEqual(state["next_blocker"], "requested_reviewers")
        self.assertNotIn("merge_ready", state)

    def test_failed_checks_block_readiness(self):
        state = review_feedback_state.state_from_pages(
            repo="base-owner/base-repo",
            pages=[fixture("failed_check.json")],
        )

        self.assertEqual(state["github_state"]["checks"][0]["conclusion"], "FAILURE")
        self.assertEqual(state["next_blocker"], "checks_not_successful")
        self.assertNotIn("merge_ready", state)

    def test_clean_pr_reports_feedback_without_merge_readiness(self):
        state = review_feedback_state.state_from_pages(
            repo="base-owner/base-repo",
            pages=[fixture("clean_pr.json")],
        )

        self.assertIsNone(state["next_blocker"])
        self.assertNotIn("merge_ready", state)

    def test_blocked_merge_state_blocks_even_when_mergeable(self):
        state = review_feedback_state.state_from_pages(
            repo="base-owner/base-repo",
            pages=[fixture("blocked_merge_state.json")],
        )

        self.assertEqual(state["github_state"]["mergeable"], "MERGEABLE")
        self.assertEqual(state["github_state"]["merge_state"], "BLOCKED")
        self.assertEqual(state["next_blocker"], "merge_state_not_clean")
        self.assertNotIn("merge_ready", state)

    def test_snapshot_excludes_unowned_history_readiness_and_decisions(self):
        state = review_feedback_state.state_from_pages(
            repo="base-owner/base-repo",
            pages=[fixture("clean_pr.json")],
        )
        self.assertEqual(state["schema_version"], 2)
        for unowned_field in (
            "cycles",
            "local_readiness",
            "local_reviews",
            "external_review_attempts",
            "review_items",
            "decisions",
            "merge_ready",
            "updated_at",
        ):
            self.assertNotIn(unowned_field, state)

    def test_rejects_paginated_identity_drift(self):
        first = fixture("paginated_page_1.json")
        second = fixture("paginated_page_2.json")

        for field, value in (
            ("number", 8),
            ("baseRefName", "release"),
            ("baseRefOid", "other-base-sha"),
            ("headRefName", "other-feature"),
            ("headRefOid", "other-head-sha"),
        ):
            with self.subTest(field=field):
                drifting = copy.deepcopy(second)
                drifting["data"]["repository"]["pullRequest"][field] = value
                with self.assertRaisesRegex(
                    review_feedback_state.IdentityError,
                    "drifted from first-page identity",
                ):
                    review_feedback_state.state_from_pages(
                        "base-owner/base-repo", [first, drifting]
                    )

    def test_rejects_paginated_repository_drift(self):
        first = fixture("paginated_page_1.json")
        second = copy.deepcopy(fixture("paginated_page_2.json"))
        second["data"]["repository"]["nameWithOwner"] = "other-owner/other-repo"

        with self.assertRaisesRegex(
            review_feedback_state.IdentityError,
            "drifted from first-page identity",
        ):
            review_feedback_state.state_from_pages(
                "base-owner/base-repo", [first, second]
            )

    def test_rejects_first_page_repository_owner_mismatch(self):
        page = fixture("clean_pr.json")
        page["data"]["repository"]["owner"]["login"] = "other-owner"

        with self.assertRaisesRegex(
            review_feedback_state.IdentityError, "owned by base-owner"
        ):
            review_feedback_state.state_from_pages(
                "base-owner/base-repo", [page], pr_number=7
            )

    def test_rejects_graphql_errors_even_when_data_is_present(self):
        page = fixture("clean_pr.json")
        page["errors"] = [{"message": "partial query failure"}]

        with self.assertRaisesRegex(
            review_feedback_state.GraphQLQueryError, "partial query failure"
        ):
            review_feedback_state.state_from_pages("base-owner/base-repo", [page])

    def test_rejects_missing_requested_connection(self):
        page = fixture("clean_pr.json")
        del page["data"]["repository"]["pullRequest"]["comments"]

        with self.assertRaisesRegex(
            review_feedback_state.ResponseShapeError, "missing requested comments"
        ):
            review_feedback_state.state_from_pages("base-owner/base-repo", [page])

    def test_rejects_null_connection_node(self):
        page = fixture("clean_pr.json")
        page["data"]["repository"]["pullRequest"]["comments"]["nodes"] = [None]

        with self.assertRaisesRegex(
            review_feedback_state.ResponseShapeError, "null or malformed comments node"
        ):
            review_feedback_state.state_from_pages("base-owner/base-repo", [page])

    def test_rejects_empty_check_node(self):
        page = fixture("clean_pr.json")
        page["data"]["repository"]["pullRequest"]["statusCheckRollup"]["contexts"][
            "nodes"
        ] = [{}]

        with self.assertRaisesRegex(
            review_feedback_state.ResponseShapeError, "null or malformed checks node"
        ):
            review_feedback_state.state_from_pages("base-owner/base-repo", [page])

    def test_rejects_unrequested_data_on_later_page(self):
        first = fixture("mixed_top_level_page_1.json")
        second = fixture("mixed_top_level_page_2.json")
        second["data"]["repository"]["pullRequest"]["reviews"] = {
            "nodes": [{"state": "APPROVED"}],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }

        with self.assertRaisesRegex(
            review_feedback_state.PaginationError, "unrequested reviews data"
        ):
            review_feedback_state.state_from_pages(
                "base-owner/base-repo", [first, second]
            )

    def test_rejects_incomplete_page_identity(self):
        incomplete = fixture("clean_pr.json")
        incomplete["data"]["repository"]["pullRequest"]["headRefOid"] = None

        with self.assertRaisesRegex(
            review_feedback_state.IdentityError,
            "incomplete pull request identity",
        ):
            review_feedback_state.state_from_pages("base-owner/base-repo", [incomplete])

    def test_rejects_missing_or_inconsistent_head_repository_identity(self):
        for label, mutate, expected_error in (
            (
                "missing",
                lambda head: head.clear(),
                "missing base or head repository owner or nameWithOwner",
            ),
            (
                "owner mismatch",
                lambda head: head.update({"owner": {"login": "other-owner"}}),
                "head repository owner does not match nameWithOwner",
            ),
        ):
            with self.subTest(label=label):
                page = fixture("fork_pr.json")
                head_repository = page["data"]["repository"]["pullRequest"][
                    "headRepository"
                ]
                mutate(head_repository)
                with self.assertRaisesRegex(
                    review_feedback_state.IdentityError, expected_error
                ):
                    review_feedback_state.state_from_pages(
                        "base-owner/base-repo", [page]
                    )

    def test_rejects_nonprogressing_top_level_cursor(self):
        first = fixture("mixed_top_level_page_1.json")
        second = copy.deepcopy(first)
        second_pr = second["data"]["repository"]["pullRequest"]
        second_pr["reviewThreads"]["nodes"] = []
        second_pr["reviews"]["nodes"] = []
        second_pr["statusCheckRollup"]["contexts"]["nodes"] = []

        calls = []

        def fake_graphql(query, variables):
            calls.append((query, variables))
            return first if len(calls) == 1 else second

        original = review_feedback_state.run_gh_graphql
        review_feedback_state.run_gh_graphql = fake_graphql
        try:
            with self.assertRaisesRegex(
                review_feedback_state.PaginationError,
                "pagination cursor did not progress",
            ):
                review_feedback_state.fetch_pages("base-owner/base-repo", 7)
        finally:
            review_feedback_state.run_gh_graphql = original

    def test_rejects_nonprogressing_hydrated_comment_cursor(self):
        top_level_page = fixture("paginated_comments_page_1.json")
        repeated_comment_page = {
            "data": {
                "node": {
                    "pullRequest": {
                        "number": 7,
                        "baseRefName": "main",
                        "headRefName": "feature",
                        "baseRefOid": "base-sha",
                        "headRefOid": "head-sha",
                        "headRepository": {
                            "nameWithOwner": "base-owner/base-repo",
                            "owner": {"login": "base-owner"},
                        },
                        "repository": {
                            "nameWithOwner": "base-owner/base-repo",
                            "owner": {"login": "base-owner"},
                        },
                    },
                    "comments": {
                        "nodes": [],
                        "pageInfo": {
                            "hasNextPage": True,
                            "endCursor": "comment-cursor",
                        },
                    },
                }
            }
        }

        def fake_graphql(query, variables):
            if "ThreadComments" in query:
                return repeated_comment_page
            return top_level_page

        original = review_feedback_state.run_gh_graphql
        review_feedback_state.run_gh_graphql = fake_graphql
        try:
            with self.assertRaisesRegex(
                review_feedback_state.PaginationError,
                "thread comments pagination cursor did not progress",
            ):
                review_feedback_state.fetch_pages("base-owner/base-repo", 7)
        finally:
            review_feedback_state.run_gh_graphql = original

    def test_rejects_excessive_top_level_pages(self):
        first = fixture("mixed_top_level_page_1.json")
        with mock.patch.object(review_feedback_state, "MAX_TOP_LEVEL_PAGES", 1):
            with mock.patch.object(
                review_feedback_state, "run_gh_graphql", return_value=first
            ):
                with self.assertRaisesRegex(
                    review_feedback_state.PaginationError,
                    "top-level pagination exceeded",
                ):
                    review_feedback_state.fetch_pages("base-owner/base-repo", 7)

    def test_rejects_excessive_thread_comment_pages(self):
        top_level = fixture("paginated_comments_page_1.json")
        continued = fixture("paginated_comments_page_2.json")
        comments = continued["data"]["repository"]["pullRequest"]["reviewThreads"][
            "nodes"
        ][0]["comments"]
        comments["pageInfo"] = {
            "hasNextPage": True,
            "endCursor": "comment-cursor-2",
        }

        def fake_graphql(query, variables):
            if "ThreadComments" in query:
                repository = continued["data"]["repository"]
                pull_request = repository["pullRequest"]
                thread = pull_request["reviewThreads"]["nodes"][0]
                identity = {
                    key: pull_request[key]
                    for key in (
                        "number",
                        "baseRefName",
                        "headRefName",
                        "baseRefOid",
                        "headRefOid",
                        "headRepository",
                    )
                }
                identity["repository"] = {
                    "nameWithOwner": repository["nameWithOwner"],
                    "owner": repository["owner"],
                }
                return {
                    "data": {
                        "node": {
                            "pullRequest": identity,
                            "comments": thread["comments"],
                        }
                    }
                }
            return top_level

        with mock.patch.object(review_feedback_state, "MAX_THREAD_COMMENT_PAGES", 1):
            with mock.patch.object(
                review_feedback_state, "run_gh_graphql", side_effect=fake_graphql
            ):
                with self.assertRaisesRegex(
                    review_feedback_state.PaginationError,
                    "thread comments pagination exceeded",
                ):
                    review_feedback_state.fetch_pages("base-owner/base-repo", 7)


if __name__ == "__main__":
    unittest.main()
