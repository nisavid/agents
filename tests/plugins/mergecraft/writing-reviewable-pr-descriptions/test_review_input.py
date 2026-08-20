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
    REPOSITORY
    / "plugins/mergecraft/skills/writing-reviewable-pr-descriptions/scripts"
)
sys.path.insert(0, str(PRODUCTION_SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from change_navigation.diff_files import manifest_rows  # noqa: E402
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
