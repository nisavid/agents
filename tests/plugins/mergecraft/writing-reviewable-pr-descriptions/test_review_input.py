from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[4]
PRODUCTION_SCRIPTS = (
    REPOSITORY / "plugins/mergecraft/skills/writing-reviewable-pr-descriptions/scripts"
)
sys.path.insert(0, str(PRODUCTION_SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from change_navigation.diff_files import manifest_rows  # noqa: E402
from change_navigation.git_observer import observe_git_diff  # noqa: E402
from change_navigation.review_input import (  # noqa: E402
    PR_NUMBER_TOKEN,
    VERSION,
    ReviewInputError,
    bind_review_input,
    load_review_input,
)
from test_validate_change_navigation import (  # noqa: E402
    DIFF,
    MODULE,
    PRODUCTION_VALIDATE,
    SCRIPT,
    STACK,
    atomic_metric,
    badge,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def seal(raw: dict[str, object]) -> dict[str, object]:
    value = copy.deepcopy(raw)
    value["content_sha256"] = digest(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    )
    return value


def manifest(
    body: str,
    *,
    title: str = "feat: widget",
    pr_number: int | str = 2,
    baseline: dict[str, object] | None = None,
) -> dict[str, object]:
    if baseline is None:
        if pr_number == PR_NUMBER_TOKEN:
            baseline = {
                "mode": "new",
                "title_sha256": None,
                "body_sha256": None,
                "fragments": [],
            }
        else:
            baseline = {
                "mode": "existing",
                "title_sha256": digest(title),
                "body_sha256": digest(body),
                "fragments": [
                    {
                        "id": "body",
                        "text": body,
                        "sha256": digest(body),
                        "disposition": "retain",
                        "replacement": None,
                        "reason": None,
                    }
                ],
            }
    diff = manifest_rows(MODULE.extract_leading_details(body.splitlines())[0])
    git_diff_by_target: dict[str, dict[str, object]] = {}
    for row in diff:
        target = str(row["target_path"])
        raw = git_diff_by_target.setdefault(
            target,
            {
                "source_path": row["source_path"],
                "target_path": target,
                "operation": "renamed"
                if row["operation"] == "MOVED"
                else "copied"
                if row["operation"] == "COPIED"
                else "modified",
                "additions": None if row["operation"] == "BINARY" else 0,
                "deletions": None if row["operation"] == "BINARY" else 0,
                "binary": row["operation"] == "BINARY",
            },
        )
        if not raw["binary"]:
            raw["additions"] = int(raw["additions"]) + int(row["additions"])
            raw["deletions"] = int(raw["deletions"]) + int(row["deletions"])
    return seal(
        {
            "version": VERSION,
            "repository": "acme/app",
            "pr_number": pr_number,
            "base": {"ref": "main", "oid": "a" * 40},
            "head": {
                "ref": "acme:widget",
                "oid": "b" * 40,
                "owner": "acme",
                "repository": "acme/app-fork",
            },
            "candidate": {
                "title": title,
                "body_sha256": digest(body),
            },
            "git_diff": sorted(
                git_diff_by_target.values(), key=lambda row: str(row["target_path"])
            ),
            "diff": diff,
            "stack": [],
            "baseline": baseline,
        }
    )


def reseal(value: dict[str, object]) -> dict[str, object]:
    return seal({key: item for key, item in value.items() if key != "content_sha256"})


def bounded_body(total: int = 101) -> str:
    summary = " ".join(
        [
            badge("DIFF", "DIFF-57606A", style="for-the-badge"),
            badge(
                f"IMPL: {total} additions, 0 deletions",
                f"IMPL-%2B{total}%20%E2%88%920-0969DA",
            ),
            badge(f"FILES: {total} touched", f"FILES-{total}-5F6B78"),
        ]
    ).replace("</picture> ", "</picture>&nbsp;", 1)
    category = " ".join(
        [
            badge(
                f"IMPL: {total} additions, 0 deletions",
                f"IMPL-%2B{total}%20%E2%88%920-0969DA",
            ),
            badge(f"FILES: {total} implementation files", f"FILES-{total}-5F6B78"),
        ]
    )
    rows = [
        f"  - [`src/{index:03d}.ts`](https://github.com/acme/app/pull/2/files#diff-"
        f"{hashlib.sha256(f'src/{index:03d}.ts'.encode()).hexdigest()}) {atomic_metric(1, 0)}"
        for index in range(100)
    ]
    comparison = "https://github.com/acme/app/compare/" + "a" * 40 + "..." + "b" * 40
    return "\n".join(
        [
            "<details>",
            f"<summary>{summary}</summary>",
            "",
            f"- {category}",
            *rows,
            f"- **{total - 100} files omitted from this bounded inventory.** "
            f"[View the complete immutable comparison]({comparison})",
            "",
            "<sup>IMPL means non-test source and configuration. TEST means automated verification. "
            "DOC means reviewer and user documentation. GEN means generated artifacts. OTHER means "
            "files outside those categories. FILES shows added, modified, and removed files as +, ~, and −.</sup>",
            "",
            "</details>",
            "",
            "## Summary",
            "- Bounded inventory.",
            "",
        ]
    )


def exact_pr39_body(
    git_diff: list[dict[str, object]],
) -> tuple[str, list[dict[str, object]]]:
    additions = sum(int(row["additions"] or 0) for row in git_diff)
    deletions = sum(int(row["deletions"] or 0) for row in git_diff)
    summary = " ".join(
        [
            badge("DIFF", "DIFF-57606A", style="for-the-badge"),
            badge(
                f"OTHER: {additions} additions, {deletions} deletions",
                f"OTHER-%2B{additions}%20%E2%88%92{deletions}-57606A",
            ),
            badge("FILES: 592 touched", "FILES-592-5F6B78"),
        ]
    ).replace("</picture> ", "</picture>&nbsp;", 1)
    category = " ".join(
        [
            badge(
                f"OTHER: {additions} additions, {deletions} deletions",
                f"OTHER-%2B{additions}%20%E2%88%92{deletions}-57606A",
            ),
            badge("FILES: 592 other files", "FILES-592-5F6B78"),
        ]
    )
    rows, categorized = [], []
    for row in git_diff:
        operation = (
            "MOVED"
            if row["operation"] == "renamed"
            else "COPIED"
            if row["operation"] == "copied"
            else "BINARY"
            if row["binary"]
            else "ATOMIC"
        )
        categorized.append(
            {
                "category": "OTHER",
                "operation": operation,
                "source_path": row["source_path"]
                if operation in {"MOVED", "COPIED"}
                else None,
                "target_path": row["target_path"],
                "additions": int(row["additions"] or 0),
                "deletions": int(row["deletions"] or 0),
            }
        )
    for row in categorized[:100]:
        target = str(row["target_path"])
        anchor = hashlib.sha256(target.encode()).hexdigest()
        if row["operation"] in {"MOVED", "COPIED"}:
            label = f"[`{row['source_path']}` → `{target}`]"
        else:
            label = f"[`{target}`]"
        metric = (
            badge(
                str(row["operation"]),
                f"{row['operation']}-5F6B78",
                title=str(row["operation"]),
            )
            if row["operation"] == "BINARY"
            else (
                (
                    badge(
                        str(row["operation"]),
                        f"{row['operation']}-5F6B78",
                        title=str(row["operation"]),
                    )
                    + " "
                )
                if row["operation"] in {"MOVED", "COPIED"}
                else ""
            )
            + atomic_metric(int(row["additions"]), int(row["deletions"]))
        )
        rows.append(
            f"  - {label}(https://github.com/nisavid/agents/pull/39/files#diff-{anchor}) {metric}"
        )
    comparison = "https://github.com/nisavid/agents/compare/29eb536f04a428d8b84a04c443c99223abb7a8e7...74772595e20dc9b218131f364dd68f762c1dfbc2"
    body = "\n".join(
        [
            "<details>",
            f"<summary>{summary}</summary>",
            "",
            f"- {category}",
            *rows,
            f"- **492 files omitted from this bounded inventory.** [View the complete immutable comparison]({comparison})",
            "",
            "<sup>IMPL means non-test source and configuration. TEST means automated verification. DOC means reviewer and user documentation. GEN means generated artifacts. OTHER means files outside those categories. FILES shows added, modified, and removed files as +, ~, and −.</sup>",
            "",
            "</details>",
            "",
            "## Summary",
            "- Exact PR #39 bounded inventory.",
            "",
        ]
    )
    return body, categorized


class ReviewInputTests(unittest.TestCase):
    def write(self, value: dict[str, object], *, raw: str | None = None) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "review-input.json"
        path.write_text(raw if raw is not None else json.dumps(value), encoding="utf-8")
        return path

    def bind(
        self,
        value: dict[str, object],
        *,
        body: str,
        stored_title: str | None = None,
        stored_body: str | None = None,
        template_body: str | None = None,
        head_repository: str = "acme/app-fork",
    ) -> None:
        bind_review_input(
            load_review_input(self.write(value)),
            repository="acme/app",
            pr_number=2,
            base="main",
            base_oid="a" * 40,
            head="acme:widget",
            head_oid="b" * 40,
            head_owner="acme",
            head_repository=head_repository,
            title="feat: widget",
            body=body,
            stored_title=stored_title,
            stored_body=stored_body,
            template_body=template_body,
        )

    def test_binds_title_complete_body_and_diff_rows(self) -> None:
        path = self.write(manifest(DIFF))
        self.assertEqual(
            PRODUCTION_VALIDATE(
                DIFF,
                "acme/app",
                2,
                title="feat: widget",
                review_input_path=path,
            ),
            [],
        )
        self.assertTrue(
            PRODUCTION_VALIDATE(
                DIFF,
                "acme/app",
                2,
                title="feat: wrong",
                review_input_path=path,
            )
        )
        fabricated = DIFF.replace("src/widget.ts", "src/fabricated.ts")
        self.assertTrue(
            PRODUCTION_VALIDATE(
                fabricated,
                "acme/app",
                2,
                title="feat: widget",
                review_input_path=path,
            )
        )

    def test_large_diff_presentation_binds_first_100_git_targets(self) -> None:
        value = manifest(DIFF)
        seed_git = value["git_diff"][0]  # type: ignore[index]
        seed_diff = value["diff"][0]  # type: ignore[index]
        value["git_diff"] = []
        value["diff"] = []
        for index in range(101):
            target = f"src/{index:03d}.ts"
            value["git_diff"].append({**seed_git, "target_path": target})  # type: ignore[union-attr]
            value["diff"].append({**seed_diff, "target_path": target})  # type: ignore[union-attr]
        value["presentation"] = {
            "selected_targets": [f"src/{index:03d}.ts" for index in range(100)],
            "omitted_count": 1,
            "comparison_url": "https://github.com/acme/app/compare/"
            + "a" * 40
            + "..."
            + "b" * 40,
        }
        loaded = load_review_input(self.write(reseal(value)))
        self.assertEqual(loaded.raw["presentation"]["omitted_count"], 1)

        for mutation, message in (
            (lambda item: item["selected_targets"].reverse(), "first 100"),
            (lambda item: item.__setitem__("omitted_count", 2), "omitted_count"),
            (
                lambda item: item.__setitem__(
                    "comparison_url",
                    "https://github.com/other/app/compare/"
                    + "a" * 40
                    + "..."
                    + "b" * 40,
                ),
                "comparison URL",
            ),
        ):
            broken = copy.deepcopy(value)
            mutation(broken["presentation"])
            with self.assertRaisesRegex(ReviewInputError, message):
                load_review_input(self.write(reseal(broken)))

    def test_rejects_presentation_for_100_or_fewer_files(self) -> None:
        value = manifest(DIFF)
        value["presentation"] = {
            "selected_targets": ["src/widget.ts"],
            "omitted_count": 0,
            "comparison_url": "https://github.com/acme/app/compare/"
            + "a" * 40
            + "..."
            + "b" * 40,
        }
        with self.assertRaisesRegex(ReviewInputError, "over 100"):
            load_review_input(self.write(reseal(value)))

    def test_requires_presentation_for_more_than_100_files(self) -> None:
        value = manifest(bounded_body())
        value["git_diff"].append(
            {
                "source_path": None,
                "target_path": "src/100.ts",
                "operation": "modified",
                "additions": 1,
                "deletions": 0,
                "binary": False,
            }
        )
        value["diff"].append(
            {
                "category": "IMPL",
                "operation": "ATOMIC",
                "source_path": None,
                "target_path": "src/100.ts",
                "additions": 1,
                "deletions": 0,
            }
        )

        with self.assertRaisesRegex(ReviewInputError, "requires presentation"):
            load_review_input(self.write(reseal(value)))

    def test_new_pr_stack_token_is_only_allowed_on_sole_current_row(self) -> None:
        template = DIFF + "\n" + PR_NUMBER_TOKEN
        value = manifest(template, pr_number=PR_NUMBER_TOKEN)
        value["stack"] = [
            {
                "number": PR_NUMBER_TOKEN,
                "title": "feat: widget",
                "url": f"https://github.com/acme/app/pull/{PR_NUMBER_TOKEN}",
                "current": True,
                "metrics": {"IMPL": [9, 3]},
                "file_operations": {
                    "added": 0,
                    "modified": 1,
                    "removed": 0,
                    "moved": 0,
                    "copied": 0,
                },
            }
        ]
        self.assertEqual(
            load_review_input(self.write(reseal(value))).raw["stack"][0]["number"],
            PR_NUMBER_TOKEN,
        )

        for change in ("noncurrent", "wrong-url", "second-current", "existing"):
            broken = copy.deepcopy(value)
            if change == "noncurrent":
                broken["stack"][0]["current"] = False
            elif change == "wrong-url":
                broken["stack"][0]["url"] = (
                    f"https://github.com/other/app/pull/{PR_NUMBER_TOKEN}"
                )
            elif change == "second-current":
                broken["stack"].append(
                    {
                        **broken["stack"][0],
                        "number": 1,
                        "url": "https://github.com/acme/app/pull/1",
                    }
                )
            else:
                broken["pr_number"] = 2
                broken["baseline"] = manifest(DIFF)["baseline"]
            with self.subTest(change=change), self.assertRaises(ReviewInputError):
                load_review_input(self.write(reseal(broken)))

    def test_new_pr_nonempty_stack_requires_token_current_identity(self) -> None:
        value = manifest(DIFF, pr_number=PR_NUMBER_TOKEN)
        value["stack"] = [
            {
                "number": 123,
                "title": "feat: widget",
                "url": "https://github.com/acme/app/pull/123",
                "current": True,
                "metrics": {"IMPL": [9, 3]},
                "file_operations": {
                    "added": 0,
                    "modified": 1,
                    "removed": 0,
                    "moved": 0,
                    "copied": 0,
                },
            }
        ]

        with self.assertRaisesRegex(ReviewInputError, "new-PR token"):
            load_review_input(self.write(reseal(value)))

        value["stack"] = []
        self.assertEqual(load_review_input(self.write(reseal(value))).raw["stack"], [])

    def test_current_stack_aggregates_must_match_full_sealed_diff(self) -> None:
        value = manifest(DIFF)
        value["stack"] = [
            {
                "number": 2,
                "title": "feat: widget",
                "url": "https://github.com/acme/app/pull/2",
                "current": True,
                "metrics": {"IMPL": [8, 3]},
                "file_operations": {
                    "added": 1,
                    "modified": 0,
                    "removed": 0,
                    "moved": 0,
                    "copied": 0,
                },
            }
        ]

        with self.assertRaisesRegex(ReviewInputError, "current Stack aggregates"):
            load_review_input(self.write(reseal(value)))

    def test_public_validator_projects_new_pr_stack_token_to_assigned_number(
        self,
    ) -> None:
        token_stack = STACK.replace("#2", f"#{PR_NUMBER_TOKEN}").replace(
            "/pull/2", f"/pull/{PR_NUMBER_TOKEN}"
        )
        token_diff = DIFF.replace("/pull/2", f"/pull/{PR_NUMBER_TOKEN}")
        template = token_stack + token_diff
        value = manifest(DIFF, pr_number=PR_NUMBER_TOKEN)
        value["candidate"]["body_sha256"] = digest(template)
        value["stack"] = [
            {
                "number": 1,
                "title": "feat: base",
                "url": "https://github.com/acme/app/pull/1",
                "current": False,
                "metrics": {"IMPL": [1, 0]},
                "file_operations": {
                    "added": 1,
                    "modified": 0,
                    "removed": 0,
                    "moved": 0,
                    "copied": 0,
                },
            },
            {
                "number": PR_NUMBER_TOKEN,
                "title": "feat: top",
                "url": f"https://github.com/acme/app/pull/{PR_NUMBER_TOKEN}",
                "current": True,
                "metrics": {"IMPL": [9, 3]},
                "file_operations": {
                    "added": 0,
                    "modified": 1,
                    "removed": 0,
                    "moved": 0,
                    "copied": 0,
                },
            },
        ]
        value = reseal(value)
        path = self.write(value)
        for assigned in (2_147_483_647, 41):
            with self.subTest(assigned=assigned):
                rendered = template.replace(PR_NUMBER_TOKEN, str(assigned))
                self.assertEqual(
                    PRODUCTION_VALIDATE(
                        rendered,
                        "acme/app",
                        assigned,
                        title="feat: widget",
                        review_input_path=path,
                        template_body=template,
                    ),
                    [],
                )

    def test_public_validator_rejects_guessed_current_stack_row_number(self) -> None:
        guessed = 2_147_483_647
        token_stack = STACK.replace("#2", f"#{guessed}").replace(
            "/pull/2", f"/pull/{guessed}"
        )
        token_diff = DIFF.replace("/pull/2", f"/pull/{PR_NUMBER_TOKEN}")
        template = token_stack + token_diff
        value = manifest(DIFF, pr_number=PR_NUMBER_TOKEN)
        value["candidate"]["body_sha256"] = digest(template)
        value["stack"] = [
            {
                "number": PR_NUMBER_TOKEN,
                "title": "feat: top",
                "url": f"https://github.com/acme/app/pull/{PR_NUMBER_TOKEN}",
                "current": True,
                "metrics": {"IMPL": [9, 3]},
                "file_operations": {
                    "added": 0,
                    "modified": 1,
                    "removed": 0,
                    "moved": 0,
                    "copied": 0,
                },
            }
        ]
        rendered = template.replace(PR_NUMBER_TOKEN, str(guessed))

        errors = PRODUCTION_VALIDATE(
            rendered,
            "acme/app",
            guessed,
            title="feat: widget",
            review_input_path=self.write(reseal(value)),
            template_body=template,
        )

        self.assertTrue(any("current Stack row" in error for error in errors))

    def test_public_validator_rejects_body_over_github_limit(self) -> None:
        errors = PRODUCTION_VALIDATE(
            DIFF + "x" * 65536,
            "acme/app",
            2,
            title="feat: widget",
            review_input_path=self.write(manifest(DIFF)),
        )
        self.assertTrue(any("65536" in error for error in errors))

    def test_public_validator_accepts_canonical_bounded_diff(self) -> None:
        body = bounded_body()
        value = manifest(body)
        value["git_diff"].append(
            {
                "source_path": None,
                "target_path": "src/100.ts",
                "operation": "modified",
                "additions": 1,
                "deletions": 0,
                "binary": False,
            }
        )
        value["diff"].append(
            {
                "category": "IMPL",
                "operation": "ATOMIC",
                "source_path": None,
                "target_path": "src/100.ts",
                "additions": 1,
                "deletions": 0,
            }
        )
        value["presentation"] = {
            "selected_targets": [f"src/{index:03d}.ts" for index in range(100)],
            "omitted_count": 1,
            "comparison_url": "https://github.com/acme/app/compare/"
            + "a" * 40
            + "..."
            + "b" * 40,
        }
        self.assertLessEqual(len(body), 65536)
        self.assertEqual(
            PRODUCTION_VALIDATE(
                body,
                "acme/app",
                2,
                title="feat: widget",
                review_input_path=self.write(reseal(value)),
            ),
            [],
        )

    def test_bounded_diff_aggregates_match_full_sealed_inventories(self) -> None:
        canonical = bounded_body()
        value = manifest(canonical)
        value["git_diff"].append(
            {
                "source_path": None,
                "target_path": "src/100.ts",
                "operation": "modified",
                "additions": 1,
                "deletions": 0,
                "binary": False,
            }
        )
        value["diff"].append(
            {
                "category": "IMPL",
                "operation": "ATOMIC",
                "source_path": None,
                "target_path": "src/100.ts",
                "additions": 1,
                "deletions": 0,
            }
        )
        value["presentation"] = {
            "selected_targets": [f"src/{index:03d}.ts" for index in range(100)],
            "omitted_count": 1,
            "comparison_url": "https://github.com/acme/app/compare/"
            + "a" * 40
            + "..."
            + "b" * 40,
        }
        mutations = {
            "touched": canonical.replace(
                badge("FILES: 101 touched", "FILES-101-5F6B78"),
                badge("FILES: 100 touched", "FILES-100-5F6B78"),
            ),
            "category totals": canonical.replace(
                badge(
                    "IMPL: 101 additions, 0 deletions",
                    "IMPL-%2B101%20%E2%88%920-0969DA",
                ),
                badge(
                    "IMPL: 100 additions, 0 deletions",
                    "IMPL-%2B100%20%E2%88%920-0969DA",
                ),
            ),
            "category file counts": canonical.replace(
                badge(
                    "FILES: 101 implementation files", "FILES-101-5F6B78"
                ),
                badge(
                    "FILES: 100 implementation files", "FILES-100-5F6B78"
                ),
            ),
        }
        for expected_error, body in mutations.items():
            broken = copy.deepcopy(value)
            broken["candidate"]["body_sha256"] = digest(body)
            with self.subTest(aggregate=expected_error):
                errors = PRODUCTION_VALIDATE(
                    body,
                    "acme/app",
                    2,
                    title="feat: widget",
                    review_input_path=self.write(reseal(broken)),
                )
                self.assertTrue(
                    any(expected_error in error for error in errors), errors
                )

    def test_bounded_omission_record_must_follow_all_category_rows(self) -> None:
        body = bounded_body()
        lines = body.splitlines()
        omission = next(line for line in lines if "files omitted" in line)
        taxonomy_index = next(
            index for index, line in enumerate(lines) if line.startswith("<sup>")
        )
        category = next(line for line in lines if "implementation files" in line)
        file_row = next(line for line in lines if line.startswith("  - "))
        lines[taxonomy_index:taxonomy_index] = [category, file_row]
        body = "\n".join(lines)
        errors = MODULE._validate_markup(body, "acme/app", 2, bounded=True)
        self.assertTrue(any("omission record" in error for error in errors), errors)

    def test_exact_pr39_592_file_body_validates_within_github_limit(self) -> None:
        git_diff = observe_git_diff(
            REPOSITORY,
            base_oid="29eb536f04a428d8b84a04c443c99223abb7a8e7",
            head_oid="74772595e20dc9b218131f364dd68f762c1dfbc2",
            require_clean=False,
        )
        self.assertEqual(len(git_diff), 592)
        body, categorized = exact_pr39_body(git_diff)
        value = manifest(
            DIFF,
            title="feat(plugins/release): adopt the Agent Plugins v1 stack",
            pr_number=39,
        )
        value.update(
            {
                "repository": "nisavid/agents",
                "base": {
                    "ref": "main",
                    "oid": "29eb536f04a428d8b84a04c443c99223abb7a8e7",
                },
                "head": {
                    "ref": "nisavid:ivan/pr-publication-review-stack-successor",
                    "oid": "74772595e20dc9b218131f364dd68f762c1dfbc2",
                    "owner": "nisavid",
                    "repository": "nisavid/agents",
                },
                "candidate": {
                    "title": "feat(plugins/release): adopt the Agent Plugins v1 stack",
                    "body_sha256": digest(body),
                },
                "git_diff": git_diff,
                "diff": categorized,
                "presentation": {
                    "selected_targets": [row["target_path"] for row in git_diff[:100]],
                    "omitted_count": 492,
                    "comparison_url": "https://github.com/nisavid/agents/compare/29eb536f04a428d8b84a04c443c99223abb7a8e7...74772595e20dc9b218131f364dd68f762c1dfbc2",
                },
                "baseline": {
                    "mode": "existing",
                    "title_sha256": digest(
                        "feat(plugins/release): adopt the Agent Plugins v1 stack"
                    ),
                    "body_sha256": digest(body),
                    "fragments": [
                        {
                            "id": "body",
                            "text": body,
                            "sha256": digest(body),
                            "disposition": "retain",
                            "replacement": None,
                            "reason": None,
                        }
                    ],
                },
            }
        )
        self.assertLessEqual(len(body), 65536)
        self.assertEqual(
            PRODUCTION_VALIDATE(
                body,
                "nisavid/agents",
                39,
                title="feat(plugins/release): adopt the Agent Plugins v1 stack",
                review_input_path=self.write(reseal(value)),
            ),
            [],
        )

    def test_rejects_content_digest_and_unsafe_paths(self) -> None:
        value = manifest(DIFF)
        value["content_sha256"] = "0" * 64
        with self.assertRaisesRegex(ReviewInputError, "content_sha256"):
            load_review_input(self.write(value))

        value = manifest(DIFF)
        value["diff"][0]["target_path"] = "bad\npath"  # type: ignore[index]
        value = seal(
            {key: item for key, item in value.items() if key != "content_sha256"}
        )
        with self.assertRaisesRegex(ReviewInputError, "CR or LF"):
            load_review_input(self.write(value))

    def test_rejects_duplicate_review_input_keys(self) -> None:
        with self.assertRaisesRegex(ReviewInputError, "duplicate key"):
            load_review_input(self.write({}, raw='{"version": 2, "version": 2}'))

    def test_rejects_non_finite_review_input_values(self) -> None:
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                with self.assertRaisesRegex(ReviewInputError, "non-finite JSON value"):
                    load_review_input(self.write({}, raw=f'{{"version": {constant}}}'))

    def test_rejects_boolean_and_float_review_input_versions(self) -> None:
        for malformed in (True, 2.0):
            with self.subTest(malformed=malformed):
                value = manifest(DIFF)
                value["version"] = malformed
                value = seal(
                    {
                        key: item
                        for key, item in value.items()
                        if key != "content_sha256"
                    }
                )
                with self.assertRaisesRegex(
                    ReviewInputError, f"version must be {VERSION}"
                ):
                    load_review_input(self.write(value))

    def test_rejects_boolean_and_float_review_input_integer_fields(self) -> None:
        for malformed in (True, 2.0):
            with self.subTest(field="pr_number", malformed=malformed):
                value = manifest(DIFF)
                value["pr_number"] = malformed
                value = seal(
                    {
                        key: item
                        for key, item in value.items()
                        if key != "content_sha256"
                    }
                )
                with self.assertRaisesRegex(ReviewInputError, "pr_number"):
                    load_review_input(self.write(value))

            for metric in ("additions", "deletions"):
                with self.subTest(field=metric, malformed=malformed):
                    value = manifest(DIFF)
                    value["diff"][0][metric] = malformed  # type: ignore[index]
                    value = seal(
                        {
                            key: item
                            for key, item in value.items()
                            if key != "content_sha256"
                        }
                    )
                    with self.assertRaisesRegex(ReviewInputError, "metrics"):
                        load_review_input(self.write(value))

            with self.subTest(field="stack.number", malformed=malformed):
                value = manifest(DIFF)
                value["stack"] = [
                    {
                        "number": malformed,
                        "title": "feat: base",
                        "url": "https://github.com/acme/app/pull/1",
                        "current": True,
                        "metrics": {},
                        "file_operations": {},
                    }
                ]
                value = seal(
                    {
                        key: item
                        for key, item in value.items()
                        if key != "content_sha256"
                    }
                )
                with self.assertRaisesRegex(ReviewInputError, "invalid identity"):
                    load_review_input(self.write(value))

    def test_allows_one_mixed_file_row_per_category(self) -> None:
        value = manifest(DIFF)
        mixed_row = dict(value["diff"][0])  # type: ignore[index]
        mixed_row["category"] = "TEST"
        original = value["diff"][0]  # type: ignore[index]
        mixed_row["additions"] = original["additions"] // 2
        mixed_row["deletions"] = original["deletions"] // 2
        original["additions"] -= mixed_row["additions"]
        original["deletions"] -= mixed_row["deletions"]
        value["diff"].append(mixed_row)  # type: ignore[index]
        value = seal(
            {key: item for key, item in value.items() if key != "content_sha256"}
        )

        loaded = load_review_input(self.write(value))

        self.assertEqual(len(loaded.raw["diff"]), 2)

    def test_publishable_validation_accepts_auditable_mixed_file(self) -> None:
        from test_navigation_integrity import split_category_diff

        body = split_category_diff()
        self.assertEqual(
            PRODUCTION_VALIDATE(
                body,
                "acme/app",
                2,
                title="feat: widget",
                review_input_path=self.write(manifest(body)),
            ),
            [],
        )

    def test_rejects_mixed_file_repeated_within_one_category(self) -> None:
        value = manifest(DIFF)
        value["diff"].append(dict(value["diff"][0]))  # type: ignore[index]
        value = seal(
            {key: item for key, item in value.items() if key != "content_sha256"}
        )
        with self.assertRaisesRegex(ReviewInputError, "unique within a category"):
            load_review_input(self.write(value))

    def test_rejects_mixed_file_operation_drift_across_categories(self) -> None:
        value = manifest(DIFF)
        mixed_row = dict(value["diff"][0])  # type: ignore[index]
        mixed_row.update(
            {
                "category": "TEST",
                "operation": "MOVED",
                "source_path": "src/old-widget.ts",
            }
        )
        value["diff"].append(mixed_row)  # type: ignore[index]
        value = seal(
            {key: item for key, item in value.items() if key != "content_sha256"}
        )
        with self.assertRaisesRegex(ReviewInputError, "reuse one operation"):
            load_review_input(self.write(value))

    def test_rejects_omitted_and_fabricated_categorized_git_paths(self) -> None:
        omitted = manifest(DIFF)
        omitted["git_diff"].append(  # type: ignore[union-attr]
            {
                "source_path": None,
                "target_path": "src/omitted.ts",
                "operation": "added",
                "additions": 1,
                "deletions": 0,
                "binary": False,
            }
        )
        omitted["git_diff"] = sorted(  # type: ignore[index]
            omitted["git_diff"],
            key=lambda row: row["target_path"],  # type: ignore[union-attr,index]
        )
        omitted = seal(
            {key: item for key, item in omitted.items() if key != "content_sha256"}
        )
        with self.assertRaisesRegex(ReviewInputError, "omits or adds Git paths"):
            load_review_input(self.write(omitted))

        fabricated = manifest(DIFF)
        extra = dict(fabricated["diff"][0])  # type: ignore[index]
        extra["target_path"] = "src/fabricated.ts"
        fabricated["diff"].append(extra)  # type: ignore[index]
        fabricated = seal(
            {key: item for key, item in fabricated.items() if key != "content_sha256"}
        )
        with self.assertRaisesRegex(ReviewInputError, "omits or adds Git paths"):
            load_review_input(self.write(fabricated))

    def test_rejects_categorized_git_metric_miscounts(self) -> None:
        value = manifest(DIFF)
        value["git_diff"][0]["additions"] += 1  # type: ignore[index]
        value = seal(
            {key: item for key, item in value.items() if key != "content_sha256"}
        )
        with self.assertRaisesRegex(ReviewInputError, "metrics drift"):
            load_review_input(self.write(value))

    def test_rejects_move_with_identical_source_and_target(self) -> None:
        value = manifest(DIFF)
        row = value["diff"][0]  # type: ignore[index]
        row["operation"] = "MOVED"
        row["source_path"] = row["target_path"]
        value = seal(
            {key: item for key, item in value.items() if key != "content_sha256"}
        )
        with self.assertRaisesRegex(ReviewInputError, "must differ"):
            load_review_input(self.write(value))

    def test_new_pr_binding_uses_exact_template_with_number_collision(self) -> None:
        template = DIFF + "\nCross-reference #2 remains literal.\n" + PR_NUMBER_TOKEN
        value = manifest(template, pr_number=PR_NUMBER_TOKEN)
        body = template.replace(PR_NUMBER_TOKEN, "2")
        self.bind(value, body=body, template_body=template)

        with self.assertRaisesRegex(ReviewInputError, "exactly derive"):
            self.bind(
                value,
                body=body.replace("Cross-reference #2", "Cross-reference #99"),
                template_body=template,
            )

    def test_existing_baseline_rejects_new_pr_number_token(self) -> None:
        baseline = {
            "mode": "existing",
            "title_sha256": digest("feat: widget"),
            "body_sha256": digest(DIFF),
            "fragments": [
                {
                    "id": "body",
                    "text": DIFF,
                    "sha256": digest(DIFF),
                    "disposition": "retain",
                    "replacement": None,
                    "reason": None,
                }
            ],
        }
        with self.assertRaisesRegex(ReviewInputError, "cannot use the new-PR"):
            load_review_input(
                self.write(
                    manifest(
                        DIFF,
                        pr_number=PR_NUMBER_TOKEN,
                        baseline=baseline,
                    )
                )
            )

    def test_new_baseline_requires_new_pr_number_token(self) -> None:
        baseline = {
            "mode": "new",
            "title_sha256": None,
            "body_sha256": None,
            "fragments": [],
        }
        with self.assertRaisesRegex(ReviewInputError, "requires the new-PR"):
            load_review_input(
                self.write(manifest(DIFF, pr_number=2, baseline=baseline))
            )

    def test_existing_baseline_candidate_binding_defers_live_preimage(self) -> None:
        value = manifest(DIFF)
        self.bind(value, body=DIFF)
        with self.assertRaisesRegex(ReviewInputError, "both live baseline"):
            self.bind(value, body=DIFF, stored_title="feat: widget")

    def test_publishable_validation_reports_missing_diff_without_crashing(self) -> None:
        value = manifest(DIFF)
        value["candidate"]["body_sha256"] = digest("")  # type: ignore[index]
        value = seal(
            {key: item for key, item in value.items() if key != "content_sha256"}
        )
        errors = PRODUCTION_VALIDATE(
            "",
            "acme/app",
            2,
            title="feat: widget",
            review_input_path=self.write(value),
        )

        self.assertTrue(any("Diff" in error for error in errors))

    def test_publishable_validation_never_echoes_secret_from_malformed_markup(
        self,
    ) -> None:
        secret = "ghp_123456789012345678901234567890"
        body = DIFF.replace("\n</details>", f"\n  - unsupported {secret}\n</details>")

        errors = PRODUCTION_VALIDATE(
            body,
            "acme/app",
            2,
            title="feat: widget",
            review_input_path=self.write(manifest(DIFF)),
        )

        self.assertEqual(
            errors,
            [
                "PR body contains a suspected credential or secret; do not echo or "
                "republish it, and require authorized removal and rotation"
            ],
        )
        self.assertNotIn(secret, "\n".join(errors))

    def test_cli_uses_a_constant_diagnostic_for_body_derived_errors(self) -> None:
        body_sentinel = "unsupported-body-sentinel-12345"
        body = DIFF.replace(
            "\n</details>", f"\n  - unsupported {body_sentinel}\n</details>"
        )
        review_input = self.write(manifest(DIFF))

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "/dev/stdin",
                "--repository",
                "acme/app",
                "--pr",
                "2",
                "--title",
                "feat: widget",
                "--review-input",
                str(review_input),
            ],
            input=body,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "ERROR: Change navigation is invalid\n")
        self.assertNotIn(body_sentinel, result.stderr)

    def test_rejects_head_repository_identity_drift(self) -> None:
        with self.assertRaisesRegex(ReviewInputError, "identity drifted"):
            self.bind(
                manifest(DIFF),
                body=DIFF,
                head_repository="acme/another-fork",
            )

    def test_existing_body_requires_exact_ordered_fragment_derivation(self) -> None:
        navigation = "<details>old navigation</details>\n"
        duplicate = "same note\n"
        stored_body = navigation + duplicate + duplicate
        candidate = DIFF + duplicate + duplicate
        fragments = [
            {
                "id": "navigation",
                "text": navigation,
                "sha256": digest(navigation),
                "disposition": "replace",
                "replacement": DIFF,
                "reason": "refresh exact pushed state",
            },
            {
                "id": "note-one",
                "text": duplicate,
                "sha256": digest(duplicate),
                "disposition": "retain",
                "replacement": None,
                "reason": None,
            },
            {
                "id": "note-two",
                "text": duplicate,
                "sha256": digest(duplicate),
                "disposition": "retain",
                "replacement": None,
                "reason": None,
            },
        ]
        baseline = {
            "mode": "existing",
            "title_sha256": digest("feat: widget"),
            "body_sha256": digest(stored_body),
            "fragments": fragments,
        }
        self.bind(
            manifest(candidate, baseline=baseline),
            body=candidate,
            stored_title="feat: widget",
            stored_body=stored_body,
        )

        missing_occurrence = copy.deepcopy(baseline)
        missing_occurrence["fragments"] = fragments[:-1]
        with self.assertRaisesRegex(ReviewInputError, "exhaustively partition"):
            self.bind(
                manifest(candidate, baseline=missing_occurrence),
                body=candidate,
                stored_title="feat: widget",
                stored_body=stored_body,
            )

        reordered = duplicate + DIFF + duplicate
        reordered_manifest = manifest(DIFF, baseline=baseline)
        reordered_manifest["candidate"]["body_sha256"] = digest(reordered)  # type: ignore[index]
        reordered_manifest = seal(
            {
                key: item
                for key, item in reordered_manifest.items()
                if key != "content_sha256"
            }
        )
        with self.assertRaisesRegex(ReviewInputError, "ordered fragment derivation"):
            self.bind(
                reordered_manifest,
                body=reordered,
                stored_title="feat: widget",
                stored_body=stored_body,
            )


if __name__ == "__main__":
    unittest.main()
