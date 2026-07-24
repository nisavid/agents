#!/usr/bin/env python3
"""Fetch and summarize thread-aware GitHub PR review state."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


class FeedbackAcquisitionError(ValueError):
    """GitHub feedback could not be acquired as a complete, consistent snapshot."""


class GraphQLQueryError(FeedbackAcquisitionError):
    """GitHub returned one or more GraphQL errors."""


class ResponseShapeError(FeedbackAcquisitionError):
    """A requested GraphQL structure or node was missing or malformed."""


class PaginationError(FeedbackAcquisitionError):
    """A paginated connection was incomplete or did not progress."""


class IdentityError(FeedbackAcquisitionError):
    """Repository or pull-request identity was missing or changed."""


CONNECTION_KEYS = ("threads", "comments", "reviews", "checks", "review_requests")
GITHUB_HOST = "github.com"
READ_TIMEOUT_SECONDS = 30
MAX_TOP_LEVEL_PAGES = 10_000
MAX_THREAD_COMMENT_PAGES = 10_000


def strict_json(content: str, source: str) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ResponseShapeError(
                    f"{source} contained duplicate JSON key: {key}"
                )
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise ResponseShapeError(f"{source} contained non-finite JSON value: {value}")

    try:
        return json.loads(
            content,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as error:
        raise ResponseShapeError(f"{source} was not valid JSON") from error


def build_pr_query(
    repo: str,
    pr_number: int,
    cursors: dict[str, str | None],
    include: dict[str, bool] | None = None,
) -> tuple[str, dict[str, Any]]:
    owner, name = split_repo(repo)
    include = include or {
        "threads": True,
        "comments": True,
        "reviews": True,
        "checks": True,
        "review_requests": True,
    }
    query = """
query PrReviewState(
  $owner: String!
  $name: String!
  $prNumber: Int!
  $threadsCursor: String
  $commentsCursor: String
  $reviewsCursor: String
  $checksCursor: String
  $reviewRequestsCursor: String
  $includeThreads: Boolean!
  $includeComments: Boolean!
  $includeReviews: Boolean!
  $includeChecks: Boolean!
  $includeReviewRequests: Boolean!
) {
  repository(owner: $owner, name: $name) {
    nameWithOwner
    owner { login }
    pullRequest(number: $prNumber) {
      number
      url
      isDraft
      baseRefName
      headRefName
      baseRefOid
      headRefOid
      headRepository {
        nameWithOwner
        owner { login }
      }
      reviewDecision
      mergeStateStatus
      mergeable
      reviewThreads(first: 100, after: $threadsCursor) @include(if: $includeThreads) {
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          comments(first: 100) {
            nodes {
              author { login }
              body
              createdAt
              url
            }
            pageInfo { hasNextPage endCursor }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
      comments(first: 100, after: $commentsCursor) @include(if: $includeComments) {
        nodes {
          author { login }
          body
          createdAt
          url
        }
        pageInfo { hasNextPage endCursor }
      }
      reviews(first: 100, after: $reviewsCursor) @include(if: $includeReviews) {
        nodes {
          author { login }
          state
          body
          submittedAt
          url
        }
        pageInfo { hasNextPage endCursor }
      }
      reviewRequests(
        first: 100
        after: $reviewRequestsCursor
      ) @include(if: $includeReviewRequests) {
        nodes {
          requestedReviewer {
            __typename
            ... on User { login }
            ... on Team { slug }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
      statusCheckRollup @include(if: $includeChecks) {
        contexts(first: 100, after: $checksCursor) {
          nodes {
            __typename
            ... on CheckRun {
              name
              status
              conclusion
            }
            ... on StatusContext {
              context
              state
            }
          }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
  }
}
""".strip()
    variables = {
        "owner": owner,
        "name": name,
        "prNumber": pr_number,
        "threadsCursor": cursors.get("threads"),
        "commentsCursor": cursors.get("comments"),
        "reviewsCursor": cursors.get("reviews"),
        "checksCursor": cursors.get("checks"),
        "reviewRequestsCursor": cursors.get("review_requests"),
        "includeThreads": bool(include.get("threads")),
        "includeComments": bool(include.get("comments")),
        "includeReviews": bool(include.get("reviews")),
        "includeChecks": bool(include.get("checks")),
        "includeReviewRequests": bool(include.get("review_requests")),
    }
    return query, variables


def split_repo(repo: str) -> tuple[str, str]:
    if re.fullmatch(r"[^/\s]+/[^/\s]+", repo) is None:
        raise ValueError("repo must be OWNER/REPO")
    owner, name = repo.split("/", 1)
    return owner, name


def validate_head_repository_identity(name_with_owner: str, owner: str) -> None:
    try:
        expected_owner, _ = split_repo(name_with_owner)
    except ValueError as error:
        raise IdentityError("head repository nameWithOwner was malformed") from error
    if owner != expected_owner:
        raise IdentityError("head repository owner does not match nameWithOwner")


def fetch_pages(repo: str, pr_number: int) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    cursors: dict[str, str | None] = {
        "threads": None,
        "comments": None,
        "reviews": None,
        "checks": None,
        "review_requests": None,
    }
    include = {key: True for key in cursors}
    expected_identity: dict[str, Any] | None = None
    seen_cursors = {key: set() for key in cursors}
    while True:
        if len(pages) >= MAX_TOP_LEVEL_PAGES:
            raise PaginationError(
                f"top-level pagination exceeded {MAX_TOP_LEVEL_PAGES} pages"
            )
        query, variables = build_pr_query(repo, pr_number, cursors, include=include)
        page = run_gh_graphql(query, variables)
        validate_pr_page(page, include, "paginated page")
        page_identity = pull_request_identity(page)
        if expected_identity is None:
            validate_requested_identity(repo, pr_number, page_identity)
            expected_identity = page_identity
        else:
            validate_matching_identity(
                expected_identity, page_identity, "paginated page"
            )
        pages.append(page)
        page_info = page_infos(page)
        next_cursors = {
            key: next_cursor(key, page_info[key], cursors[key], seen_cursors[key])
            for key in cursors
        }
        if not any(next_cursors.values()):
            hydrate_thread_comments(pages, expected_identity)
            return pages
        include = {key: value is not None for key, value in next_cursors.items()}
        cursors = next_cursors


def hydrate_thread_comments(
    pages: list[dict[str, Any]], expected_identity: dict[str, Any]
) -> None:
    for page in pages:
        for thread in (extract_pr(page).get("reviewThreads") or {}).get("nodes") or []:
            comments = thread.get("comments") or {}
            page_info = comments.get("pageInfo") or {}
            seen_cursors: set[str] = set()
            cursor: str | None = None
            page_count = 0
            while page_info.get("hasNextPage"):
                if page_count >= MAX_THREAD_COMMENT_PAGES:
                    raise PaginationError(
                        "thread comments pagination exceeded "
                        f"{MAX_THREAD_COMMENT_PAGES} pages"
                    )
                cursor = next_cursor("thread comments", page_info, cursor, seen_cursors)
                next_page = fetch_thread_comments(
                    thread["id"], page_info.get("endCursor")
                )
                validate_thread_comments_page(
                    next_page, f"thread {thread['id']} comment page"
                )
                validate_matching_identity(
                    expected_identity,
                    thread_comments_identity(next_page),
                    f"thread {thread['id']} comment page",
                )
                next_comments = ((next_page.get("data") or {}).get("node") or {}).get(
                    "comments"
                ) or {}
                comments.setdefault("nodes", []).extend(
                    next_comments.get("nodes") or []
                )
                page_info = next_comments.get("pageInfo") or {}
                comments["pageInfo"] = page_info
                page_count += 1


def fetch_thread_comments(thread_id: str, cursor: str | None) -> dict[str, Any]:
    query = """
query ThreadComments($threadId: ID!, $commentsCursor: String) {
  node(id: $threadId) {
    ... on PullRequestReviewThread {
      pullRequest {
        number
        baseRefName
        headRefName
        baseRefOid
        headRefOid
        headRepository {
          nameWithOwner
          owner { login }
        }
        repository {
          nameWithOwner
          owner { login }
        }
      }
      comments(first: 100, after: $commentsCursor) {
        nodes {
          author { login }
          body
          createdAt
          url
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
""".strip()
    page = run_gh_graphql(query, {"threadId": thread_id, "commentsCursor": cursor})
    validate_thread_comments_page(page, f"thread {thread_id} comment page")
    return page


def run_gh_graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    command = [
        "gh",
        "api",
        "--hostname",
        GITHUB_HOST,
        "graphql",
        "--input",
        "-",
    ]
    request = json.dumps(
        {"query": query, "variables": variables},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    try:
        environment = dict(os.environ)
        environment.pop("GH_HOST", None)
        environment.pop("GH_REPO", None)
        environment.update({"GH_PROMPT_DISABLED": "1", "GIT_TERMINAL_PROMPT": "0"})
        completed = subprocess.run(
            command,
            check=False,
            input=request,
            text=True,
            capture_output=True,
            timeout=READ_TIMEOUT_SECONDS,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise FeedbackAcquisitionError(
            f"could not run gh GraphQL acquisition: {error}"
        ) from error
    if completed.returncode:
        detail = (
            completed.stderr.strip()
            or completed.stdout.strip()
            or "gh api graphql failed"
        )
        raise FeedbackAcquisitionError(detail)
    response = strict_json(completed.stdout, "GraphQL response")
    validate_graphql_envelope(response, "GraphQL response")
    return response


def validate_graphql_envelope(response: Any, source: str) -> None:
    if not isinstance(response, dict):
        raise ResponseShapeError(f"{source} was not an object")
    if "errors" in response:
        errors = response["errors"]
        if not isinstance(errors, list):
            raise ResponseShapeError(f"{source} had malformed GraphQL errors")
        if errors:
            messages = [
                error.get("message", "unknown GraphQL error")
                if isinstance(error, dict)
                else "malformed GraphQL error"
                for error in errors
            ]
            raise GraphQLQueryError(f"{source} reported errors: {'; '.join(messages)}")
    if not isinstance(response.get("data"), dict):
        raise ResponseShapeError(f"{source} was missing GraphQL data")


def validate_pr_page(page: Any, include: dict[str, bool], source: str) -> None:
    validate_graphql_envelope(page, source)
    try:
        repository = page["data"]["repository"]
    except (KeyError, TypeError) as error:
        raise ResponseShapeError(f"{source} was missing repository data") from error
    if not isinstance(repository, dict) or not repository:
        raise ResponseShapeError(f"{source} repository was null or malformed")
    owner = repository.get("owner")
    if (
        not isinstance(repository.get("nameWithOwner"), str)
        or not repository["nameWithOwner"]
        or not isinstance(owner, dict)
        or not isinstance(owner.get("login"), str)
        or not owner["login"]
    ):
        raise IdentityError(
            f"{source} was missing repository.owner.login or nameWithOwner"
        )

    pr = extract_pr(page)
    pull_request_identity(page)
    _validate_pr_scalars(pr, source)
    for connection_name in CONNECTION_KEYS:
        requested = bool(include.get(connection_name))
        connection = _pr_connection(pr, connection_name, requested, source)
        if connection is None:
            continue
        nodes, page_info = _validate_connection(connection, connection_name, source)
        if not requested and (nodes or page_info["hasNextPage"]):
            raise PaginationError(
                f"{source} returned unrequested {connection_name} data"
            )


def validate_thread_comments_page(page: Any, source: str) -> None:
    validate_graphql_envelope(page, source)
    try:
        node = page["data"]["node"]
    except (KeyError, TypeError) as error:
        raise ResponseShapeError(f"{source} was missing its thread node") from error
    if not isinstance(node, dict) or not node:
        raise ResponseShapeError(f"{source} thread node was null or malformed")
    thread_comments_identity(page)
    comments = node.get("comments")
    if not isinstance(comments, dict):
        raise ResponseShapeError(f"{source} was missing requested comments")
    _validate_connection(comments, "comments", source)


def _validate_pr_scalars(pr: dict[str, Any], source: str) -> None:
    required_strings = (
        "url",
        "baseRefName",
        "headRefName",
        "baseRefOid",
        "headRefOid",
        "mergeStateStatus",
        "mergeable",
    )
    if (
        type(pr.get("number")) is not int
        or pr["number"] <= 0
        or not isinstance(pr.get("isDraft"), bool)
        or any(
            not isinstance(pr.get(field), str) or not pr[field]
            for field in required_strings
        )
        or (
            pr.get("reviewDecision") is not None
            and not isinstance(pr["reviewDecision"], str)
        )
    ):
        raise ResponseShapeError(f"{source} had malformed pull request fields")


def _pr_connection(
    pr: dict[str, Any], connection_name: str, required: bool, source: str
) -> dict[str, Any] | None:
    field_names = {
        "threads": "reviewThreads",
        "comments": "comments",
        "reviews": "reviews",
        "review_requests": "reviewRequests",
    }
    if connection_name == "checks":
        if "statusCheckRollup" not in pr:
            if required:
                raise ResponseShapeError(f"{source} was missing requested checks")
            return None
        rollup = pr["statusCheckRollup"]
        if rollup is None:
            return {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}
        if not isinstance(rollup, dict) or not isinstance(rollup.get("contexts"), dict):
            raise ResponseShapeError(f"{source} had malformed requested checks")
        return rollup["contexts"]

    field_name = field_names[connection_name]
    if field_name not in pr or pr[field_name] is None:
        if required:
            raise ResponseShapeError(
                f"{source} was missing requested {connection_name}"
            )
        return None
    if not isinstance(pr[field_name], dict):
        raise ResponseShapeError(f"{source} had malformed requested {connection_name}")
    return pr[field_name]


def _validate_connection(
    connection: dict[str, Any], connection_name: str, source: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    nodes = connection.get("nodes")
    page_info = connection.get("pageInfo")
    if not isinstance(nodes, list) or not isinstance(page_info, dict):
        raise ResponseShapeError(f"{source} had malformed {connection_name} connection")
    if (
        not isinstance(page_info.get("hasNextPage"), bool)
        or "endCursor" not in page_info
        or (
            page_info["endCursor"] is not None
            and not isinstance(page_info["endCursor"], str)
        )
    ):
        raise ResponseShapeError(f"{source} had malformed {connection_name} pageInfo")
    if page_info["hasNextPage"] and not page_info["endCursor"]:
        raise PaginationError(f"{connection_name} pagination has no end cursor")
    for index, node in enumerate(nodes):
        if not isinstance(node, dict) or not node:
            raise ResponseShapeError(
                f"{source} had null or malformed {connection_name} node {index}"
            )
        _validate_connection_node(node, connection_name, source, index)
    return nodes, page_info


def _validate_connection_node(  # noqa: C901
    node: dict[str, Any], connection_name: str, source: str, index: int
) -> None:
    if connection_name == "threads":
        if (
            not isinstance(node.get("id"), str)
            or not node["id"]
            or not isinstance(node.get("isResolved"), bool)
            or not isinstance(node.get("isOutdated"), bool)
            or not isinstance(node.get("comments"), dict)
        ):
            raise ResponseShapeError(f"{source} had malformed thread node {index}")
        _validate_connection(
            node["comments"], "comments", f"{source} thread {node['id']}"
        )
    elif connection_name == "comments":
        if (
            not isinstance(node.get("body"), str)
            or not isinstance(node.get("createdAt"), str)
            or not node["createdAt"]
            or not isinstance(node.get("url"), str)
            or not node["url"]
        ):
            raise ResponseShapeError(f"{source} had malformed comment node {index}")
    elif connection_name == "reviews":
        if not isinstance(node.get("state"), str) or not node["state"]:
            raise ResponseShapeError(f"{source} had malformed review node {index}")
    elif connection_name == "review_requests":
        requested = node.get("requestedReviewer")
        if not isinstance(requested, dict) or requested.get("__typename") not in {
            "User",
            "Team",
        }:
            raise ResponseShapeError(
                f"{source} had malformed review request node {index}"
            )
        identifier = (
            requested.get("login")
            if requested["__typename"] == "User"
            else requested.get("slug")
        )
        if not isinstance(identifier, str) or not identifier:
            raise ResponseShapeError(
                f"{source} had malformed review request node {index}"
            )
    elif connection_name == "checks":
        typename = node.get("__typename")
        if typename == "CheckRun":
            valid = (
                isinstance(node.get("name"), str)
                and bool(node["name"])
                and isinstance(node.get("status"), str)
            )
        elif typename == "StatusContext":
            valid = (
                isinstance(node.get("context"), str)
                and bool(node["context"])
                and isinstance(node.get("state"), str)
            )
        else:
            valid = False
        if not valid:
            raise ResponseShapeError(
                f"{source} had empty or malformed check node {index}"
            )


def validate_supplied_page_sequence(pages: list[dict[str, Any]]) -> None:
    include = {key: True for key in CONNECTION_KEYS}
    cursors: dict[str, str | None] = {key: None for key in CONNECTION_KEYS}
    seen_cursors = {key: set() for key in CONNECTION_KEYS}
    for index, page in enumerate(pages):
        source = f"supplied page {index + 1}"
        validate_pr_page(page, include, source)
        page_info = page_infos(page)
        next_cursors = {
            key: next_cursor(key, page_info[key], cursors[key], seen_cursors[key])
            for key in CONNECTION_KEYS
        }
        if not any(next_cursors.values()) and index != len(pages) - 1:
            raise PaginationError(
                f"{source} was terminal but additional pages followed"
            )
        include = {key: value is not None for key, value in next_cursors.items()}
        cursors = next_cursors
    if any(cursors.values()):
        raise PaginationError("supplied pages ended before pagination completed")
    merged_threads = collect_review_threads([extract_pr(page) for page in pages])
    if any(
        (thread["comments"]["pageInfo"] or {}).get("hasNextPage")
        for thread in merged_threads
    ):
        raise PaginationError(
            "supplied pages ended before thread comment hydration completed"
        )


def state_from_pages(
    repo: str, pages: list[dict[str, Any]], pr_number: int | None = None
) -> dict[str, Any]:
    owner, name = split_repo(repo)
    if not pages:
        raise ResponseShapeError("at least one page is required")

    validate_supplied_page_sequence(pages)
    first_identity = pull_request_identity(pages[0])
    validate_requested_identity(
        repo,
        first_identity["number"] if pr_number is None else pr_number,
        first_identity,
    )
    for page in pages[1:]:
        validate_matching_identity(
            first_identity, pull_request_identity(page), "supplied page"
        )

    pull_requests = [extract_pr(page) for page in pages]
    pr = pull_requests[0]
    review_threads = collect_review_threads(pull_requests)
    comments = collect_nodes(pull_requests, "comments")
    reviews = collect_nodes(pull_requests, "reviews")
    review_requests = collect_nodes(pull_requests, "reviewRequests")
    checks = collect_checks(pull_requests)
    unresolved_threads = [
        summarize_thread(thread, pr["headRefOid"])
        for thread in review_threads
        if not thread.get("isResolved")
    ]
    requested_reviewers, requested_teams = summarize_review_requests(review_requests)
    normalized_checks = [normalize_check(check) for check in checks]
    pagination_complete = is_pagination_complete(pull_requests)
    next_blocker = classify_blocker(
        pr,
        unresolved_threads,
        requested_reviewers,
        requested_teams,
        normalized_checks,
        pagination_complete,
    )

    return {
        "schema_version": 2,
        "repo": {
            "owner": owner,
            "name": name,
            "head_repository": first_identity["head_repo"],
            "head_owner": first_identity["head_owner"],
            "base_ref": pr.get("baseRefName"),
            "head_ref": pr.get("headRefName"),
        },
        "pr": {
            "number": pr.get("number"),
            "url": pr.get("url"),
            "draft": bool(pr.get("isDraft")),
        },
        "diff": {
            "head_sha": pr.get("headRefOid"),
            "base_sha": pr.get("baseRefOid"),
            "diff_id": pr.get("headRefOid"),
        },
        "github_state": {
            "merge_state": pr.get("mergeStateStatus"),
            "mergeable": pr.get("mergeable"),
            "review_decision": pr.get("reviewDecision"),
            "requested_reviewers": requested_reviewers,
            "requested_teams": requested_teams,
            "checks": normalized_checks,
            "unresolved_threads": unresolved_threads,
            "comments": summarize_comments(comments),
            "reviews": summarize_reviews(reviews),
            "pagination_complete": pagination_complete,
        },
        "next_blocker": next_blocker,
    }


def extract_pr(page: dict[str, Any]) -> dict[str, Any]:
    validate_graphql_envelope(page, "pull request page")
    try:
        pr = page["data"]["repository"]["pullRequest"]
    except (KeyError, TypeError) as error:
        raise ResponseShapeError(f"missing pullRequest in page: {error}") from error
    if not isinstance(pr, dict) or not pr:
        raise ResponseShapeError("pullRequest was null or malformed")
    return pr


def pull_request_identity(page: dict[str, Any]) -> dict[str, Any]:
    try:
        repository = page["data"]["repository"]
        pr = repository["pullRequest"]
        owner = (repository.get("owner") or {}).get("login")
        name_with_owner = repository.get("nameWithOwner")
        head_repository = pr.get("headRepository") or {}
        head_owner = (head_repository.get("owner") or {}).get("login")
        head_name_with_owner = head_repository.get("nameWithOwner")
    except (KeyError, TypeError) as error:
        raise IdentityError(f"missing repository identity in page: {error}") from error
    if not owner or not name_with_owner or not head_owner or not head_name_with_owner:
        raise IdentityError(
            "missing base or head repository owner or nameWithOwner in page"
        )
    validate_head_repository_identity(head_name_with_owner, head_owner)
    identity = {
        "repo": name_with_owner,
        "owner": owner,
        "head_repo": head_name_with_owner,
        "head_owner": head_owner,
        "number": pr.get("number"),
        "base_ref": pr.get("baseRefName"),
        "base_oid": pr.get("baseRefOid"),
        "head_ref": pr.get("headRefName"),
        "head_oid": pr.get("headRefOid"),
    }
    require_complete_identity(identity, "page")
    return identity


def thread_comments_identity(page: dict[str, Any]) -> dict[str, Any]:
    try:
        pr = page["data"]["node"]["pullRequest"]
        repository = pr["repository"]
        owner = (repository.get("owner") or {}).get("login")
        name_with_owner = repository.get("nameWithOwner")
        head_repository = pr.get("headRepository") or {}
        head_owner = (head_repository.get("owner") or {}).get("login")
        head_name_with_owner = head_repository.get("nameWithOwner")
    except (KeyError, TypeError) as error:
        raise IdentityError(
            f"missing pull request identity in thread comment page: {error}"
        ) from error
    if not owner or not name_with_owner or not head_owner or not head_name_with_owner:
        raise IdentityError(
            "missing base or head repository owner or nameWithOwner "
            "in thread comment page"
        )
    validate_head_repository_identity(head_name_with_owner, head_owner)
    identity = {
        "repo": name_with_owner,
        "owner": owner,
        "head_repo": head_name_with_owner,
        "head_owner": head_owner,
        "number": pr.get("number"),
        "base_ref": pr.get("baseRefName"),
        "base_oid": pr.get("baseRefOid"),
        "head_ref": pr.get("headRefName"),
        "head_oid": pr.get("headRefOid"),
    }
    require_complete_identity(identity, "thread comment page")
    return identity


def require_complete_identity(identity: dict[str, Any], source: str) -> None:
    required_fields = (
        "repo",
        "owner",
        "head_repo",
        "head_owner",
        "number",
        "base_ref",
        "base_oid",
        "head_ref",
        "head_oid",
    )
    malformed_fields = [
        field
        for field in required_fields
        if (
            type(identity.get(field)) is not int or identity[field] <= 0
            if field == "number"
            else not isinstance(identity.get(field), str) or not identity[field]
        )
    ]
    if malformed_fields:
        raise IdentityError(
            f"{source} has incomplete pull request identity: "
            f"{', '.join(malformed_fields)}"
        )


def validate_requested_identity(
    repo: str, pr_number: int, identity: dict[str, Any]
) -> None:
    expected_owner, _ = split_repo(repo)
    if (
        identity["repo"] != repo
        or identity["owner"] != expected_owner
        or identity["number"] != pr_number
    ):
        raise IdentityError(
            "first page does not match requested pull request: "
            f"expected {repo}#{pr_number} owned by {expected_owner}, "
            f"got {identity['repo']}#{identity['number']} owned by {identity['owner']}"
        )


def validate_matching_identity(
    expected: dict[str, Any], actual: dict[str, Any], source: str
) -> None:
    differing_fields = [
        key
        for key, value in expected.items()
        if type(actual.get(key)) is not type(value) or actual.get(key) != value
    ]
    if differing_fields:
        raise IdentityError(
            f"{source} drifted from first-page identity: {', '.join(differing_fields)}"
        )


def collect_nodes(
    pull_requests: list[dict[str, Any]], key: str
) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for pr in pull_requests:
        nodes.extend((pr.get(key) or {}).get("nodes") or [])
    return nodes


def collect_review_threads(pull_requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    threads_by_id: dict[str, dict[str, Any]] = {}
    anonymous_threads: list[dict[str, Any]] = []
    for pr in pull_requests:
        for thread in (pr.get("reviewThreads") or {}).get("nodes") or []:
            thread_id = thread.get("id")
            if not thread_id:
                anonymous_threads.append(thread)
                continue
            if thread_id not in threads_by_id:
                threads_by_id[thread_id] = copy.deepcopy(thread)
                continue
            existing = threads_by_id[thread_id]
            existing["isResolved"] = thread.get(
                "isResolved", existing.get("isResolved")
            )
            existing["isOutdated"] = thread.get(
                "isOutdated", existing.get("isOutdated")
            )
            existing["path"] = thread.get("path") or existing.get("path")
            existing["line"] = (
                thread.get("line")
                if thread.get("line") is not None
                else existing.get("line")
            )
            existing_comments = existing.setdefault("comments", {}).setdefault(
                "nodes", []
            )
            seen_urls = {comment.get("url") for comment in existing_comments}
            for comment in (thread.get("comments") or {}).get("nodes") or []:
                comment_url = comment.get("url")
                if comment_url not in seen_urls:
                    existing_comments.append(comment)
                    seen_urls.add(comment_url)
            existing["comments"]["pageInfo"] = (
                (thread.get("comments") or {}).get("pageInfo")
                or existing["comments"].get("pageInfo")
                or {}
            )
    return list(threads_by_id.values()) + anonymous_threads


def collect_checks(pull_requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for pr in pull_requests:
        rollup = pr.get("statusCheckRollup") or {}
        contexts = rollup.get("contexts") or {}
        nodes.extend(contexts.get("nodes") or [])
    return nodes


def summarize_thread(thread: dict[str, Any], diff_id: str | None) -> dict[str, Any]:
    comments = (thread.get("comments") or {}).get("nodes") or []
    latest = comments[-1] if comments else {}
    first = comments[0] if comments else {}
    latest_author = (latest.get("author") or {}).get("login")
    first_body = (first.get("body") or "").strip().splitlines()
    return {
        "id": thread.get("id"),
        "url": latest.get("url"),
        "path": thread.get("path"),
        "line": thread.get("line"),
        "author": ((first.get("author") or {}).get("login")),
        "latest_comment_author": latest_author,
        "latest_comment_created_at": latest.get("createdAt"),
        "summary": first_body[0] if first_body else "",
        "is_outdated": bool(thread.get("isOutdated")),
        "is_resolved": bool(thread.get("isResolved")),
        "associated_diff_id": diff_id,
    }


def summarize_review_requests(
    nodes: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    reviewers: list[str] = []
    teams: list[str] = []
    for node in nodes:
        requested = node.get("requestedReviewer") or {}
        if requested.get("__typename") == "Team":
            if requested.get("slug"):
                teams.append(requested["slug"])
        elif requested.get("login"):
            reviewers.append(requested["login"])
    return reviewers, teams


def normalize_check(node: dict[str, Any]) -> dict[str, Any]:
    if node.get("__typename") == "StatusContext":
        state = node.get("state")
        conclusion = "SUCCESS" if state == "SUCCESS" else state
        return {
            "name": node.get("context"),
            "status": state,
            "conclusion": conclusion,
        }
    return {
        "name": node.get("name"),
        "status": node.get("status"),
        "conclusion": node.get("conclusion"),
    }


def summarize_reviews(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "author": (node.get("author") or {}).get("login"),
            "state": node.get("state"),
            "body": node.get("body") or "",
            "submitted_at": node.get("submittedAt"),
            "url": node.get("url"),
        }
        for node in nodes
    ]


def summarize_comments(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "author": (node.get("author") or {}).get("login"),
            "body": node.get("body") or "",
            "created_at": node.get("createdAt"),
            "url": node.get("url"),
        }
        for node in nodes
    ]


def classify_blocker(
    pr: dict[str, Any],
    unresolved_threads: list[dict[str, Any]],
    requested_reviewers: list[str],
    requested_teams: list[str],
    checks: list[dict[str, Any]],
    pagination_complete: bool,
) -> str | None:
    if not pagination_complete:
        return "pagination_incomplete"
    if pr.get("isDraft"):
        return "draft_pr"
    if requested_reviewers or requested_teams:
        return "requested_reviewers"
    active_threads = [
        thread for thread in unresolved_threads if not thread["is_outdated"]
    ]
    outdated_threads = [
        thread for thread in unresolved_threads if thread["is_outdated"]
    ]
    if active_threads:
        return "unresolved_review_threads"
    if outdated_threads:
        return "outdated_unresolved_review_threads"
    if any(not check_successful(check) for check in checks):
        return "checks_not_successful"
    review_decision = pr.get("reviewDecision")
    if review_decision not in (None, "", "APPROVED"):
        return "review_not_approved"
    merge_state = pr.get("mergeStateStatus")
    mergeable = pr.get("mergeable")
    if merge_state not in ("CLEAN", "HAS_HOOKS"):
        return "merge_state_not_clean"
    if mergeable not in (None, "MERGEABLE"):
        return "merge_state_not_clean"
    return None


def check_successful(check: dict[str, Any]) -> bool:
    status = check.get("status")
    conclusion = check.get("conclusion")
    return status in (None, "COMPLETED", "SUCCESS") and conclusion in (
        None,
        "SUCCESS",
        "NEUTRAL",
        "SKIPPED",
    )


def page_infos(page: dict[str, Any]) -> dict[str, dict[str, Any]]:
    pr = extract_pr(page)
    rollup = pr.get("statusCheckRollup") or {}
    contexts = rollup.get("contexts") or {}
    return {
        "threads": ((pr.get("reviewThreads") or {}).get("pageInfo") or {}),
        "comments": ((pr.get("comments") or {}).get("pageInfo") or {}),
        "reviews": ((pr.get("reviews") or {}).get("pageInfo") or {}),
        "checks": (contexts.get("pageInfo") or {}),
        "review_requests": ((pr.get("reviewRequests") or {}).get("pageInfo") or {}),
    }


def next_cursor(
    connection: str,
    info: dict[str, Any],
    current_cursor: str | None,
    seen_cursors: set[str],
) -> str | None:
    if not info.get("hasNextPage"):
        return None
    cursor = info.get("endCursor")
    if not isinstance(cursor, str) or not cursor:
        raise PaginationError(f"{connection} pagination has no end cursor")
    if cursor == current_cursor or cursor in seen_cursors:
        raise PaginationError(f"{connection} pagination cursor did not progress")
    seen_cursors.add(cursor)
    return cursor


def is_pagination_complete(pull_requests: list[dict[str, Any]]) -> bool:
    if not pull_requests:
        return False
    last_infos = page_infos(
        {"data": {"repository": {"pullRequest": pull_requests[-1]}}}
    )
    top_level_complete = not any(
        (info or {}).get("hasNextPage") for info in last_infos.values()
    )
    comments_complete = True
    for thread in collect_review_threads(pull_requests):
        comments_info = (thread.get("comments") or {}).get("pageInfo") or {}
        if comments_info.get("hasNextPage"):
            comments_complete = False
            break
    return top_level_complete and comments_complete


def summary_text(state: dict[str, Any]) -> str:
    requested_reviewers = (
        ", ".join(state["github_state"]["requested_reviewers"]) or "none"
    )
    requested_teams = ", ".join(state["github_state"]["requested_teams"]) or "none"
    lines = [
        f"PR: {state['pr']['url']}",
        f"head_repository: {state['repo']['head_repository']}",
        f"next_blocker: {state['next_blocker'] or 'none'}",
        f"review_decision: {state['github_state']['review_decision'] or 'none'}",
        f"merge_state: {state['github_state']['merge_state'] or 'unknown'}",
        f"requested_reviewers: {requested_reviewers}",
        f"requested_teams: {requested_teams}",
        f"unresolved_threads: {len(state['github_state']['unresolved_threads'])}",
        f"checks: {len(state['github_state']['checks'])}",
    ]
    for thread in state["github_state"]["unresolved_threads"]:
        status = "outdated" if thread["is_outdated"] else "active"
        location = thread.get("path") or "unknown-path"
        if thread.get("line") is not None:
            location = f"{location}:{thread['line']}"
        lines.append(
            "unresolved_thread: "
            f"{status} {location} "
            f"author={thread.get('author') or 'unknown'} "
            f"url={thread.get('url') or 'none'} "
            f"summary={thread.get('summary') or ''}"
        )
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Base repository as OWNER/REPO")
    parser.add_argument(
        "--pr",
        required=True,
        type=int,
        help="Pull request number in the base repository",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON state")
    parser.add_argument(
        "--summary", action="store_true", help="Print terse human summary"
    )
    parser.add_argument(
        "--fixture",
        action="append",
        type=Path,
        help="Read one or more fixture pages instead of calling gh",
    )
    return parser.parse_args(argv)


def load_fixture_page(path: Path) -> dict[str, Any]:
    try:
        page = strict_json(path.read_text(encoding="utf-8"), f"fixture page {path}")
    except (OSError, ResponseShapeError) as error:
        raise ResponseShapeError(
            f"could not load fixture page {path}: {error}"
        ) from error
    if not isinstance(page, dict):
        raise ResponseShapeError(f"fixture page {path} was not an object")
    return page


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.fixture:
        pages = [load_fixture_page(path) for path in args.fixture]
    else:
        pages = fetch_pages(args.repo, args.pr)
    state = state_from_pages(args.repo, pages, pr_number=args.pr)
    if args.json or not args.summary:
        print(json.dumps(state, indent=2, sort_keys=True))
    if args.summary:
        print(summary_text(state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
