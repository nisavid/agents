from __future__ import annotations

import json
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
VALIDATOR = REPOSITORY / "scripts" / "validate_task_witness.py"
SOURCE_SHAPE_RECORD = Path("release/task-witness/source-shape-review.json")


class TaskWitnessPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repository = Path(self.temporary.name).resolve() / "repository"
        self.plugin = self.repository / "plugins" / "task-witness"
        shutil.copytree(REPOSITORY / "plugins" / "task-witness", self.plugin)
        (self.repository / "scripts").mkdir(parents=True)
        shutil.copy2(VALIDATOR, self.repository / "scripts" / VALIDATOR.name)
        record = self.repository / SOURCE_SHAPE_RECORD
        record.parent.mkdir(parents=True)
        shutil.copy2(REPOSITORY / SOURCE_SHAPE_RECORD, record)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def validate(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(self.repository / "scripts" / VALIDATOR.name),
                str(self.repository),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_rejected(self, expected: str) -> None:
        result = self.validate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(expected, result.stderr)

    def test_current_package_passes_without_generated_bytecode(self) -> None:
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(list(self.plugin.rglob("__pycache__")))
        self.assertFalse(list(self.plugin.rglob("*.pyc")))

    def test_rejects_claude_manifest_identity_drift(self) -> None:
        path = self.plugin / ".claude-plugin" / "plugin.json"
        manifest = json.loads(path.read_text())
        manifest["version"] = "0.1.1"
        path.write_text(json.dumps(manifest))
        self.assert_rejected("manifest shared identity drift")

    def test_rejects_codex_manifest_schema_drift(self) -> None:
        path = self.plugin / ".codex-plugin" / "plugin.json"
        manifest = json.loads(path.read_text())
        manifest["interface"]["category"] = "Other"
        path.write_text(json.dumps(manifest))
        self.assert_rejected("Codex manifest contract drift")

    def test_rejects_duplicate_and_nonfinite_manifest_json(self) -> None:
        claude = self.plugin / ".claude-plugin" / "plugin.json"
        claude.write_text('{"name":"task-witness","name":"shadow"}')
        self.assert_rejected("duplicate key")

        shutil.copytree(
            REPOSITORY / "plugins" / "task-witness", self.plugin, dirs_exist_ok=True
        )
        codex = self.plugin / ".codex-plugin" / "plugin.json"
        codex.write_text('{"name":"task-witness","version":1e999}')
        self.assert_rejected("non-finite")

    def test_rejects_extra_code_only_inventory_entries(self) -> None:
        (self.plugin / "runtime" / "helper.py").write_text("pass\n")
        self.assert_rejected("code-only inventory drift")

    def test_rejects_skill_surface_and_generated_python_state(self) -> None:
        (self.plugin / "skills").mkdir()
        self.assert_rejected("code-only inventory drift")

        shutil.rmtree(self.plugin / "skills")
        (self.plugin / "runtime" / "task_witness.pyc").write_bytes(b"pyc")
        self.assert_rejected("code-only inventory drift")

    def test_rejects_symlinked_package_entries(self) -> None:
        runtime = self.plugin / "runtime" / "task_witness.py"
        linked = self.plugin / "runtime" / "runtime-link.py"
        linked.symlink_to(runtime)
        self.assert_rejected("code-only inventory drift")

    def test_rejects_source_module_that_exceeds_the_review_line_limit(self) -> None:
        runtime = self.plugin / "runtime" / "canonical.py"
        runtime.write_text(
            "\n".join(f"line_{number} = {number}" for number in range(626)) + "\n"
        )
        self.assert_rejected("module source-line tripwire exceeded")

    def test_rejects_aggregate_source_growth_above_the_recorded_tripwire(self) -> None:
        for relative in (
            "launcher/task_witness_launch.py",
            "runtime/task_witness.py",
            "runtime/canonical.py",
        ):
            (self.plugin / relative).write_text(
                "\n".join(f"line_{number} = {number}" for number in range(500)) + "\n"
            )
        self.assert_rejected("aggregate source-line tripwire exceeded")

    def test_rejects_source_reduction_without_a_new_review_record(self) -> None:
        runtime = self.plugin / "runtime" / "canonical.py"
        runtime.write_text("pass\n")
        self.assert_rejected("source-line measurement drift")

    def test_rejects_line_count_neutral_source_byte_drift(self) -> None:
        runtime = self.plugin / "runtime" / "canonical.py"
        original = runtime.read_text(encoding="utf-8")
        runtime.write_text(
            original.replace("Task Witness", "TaskWitnesS", 1), encoding="utf-8"
        )
        self.assert_rejected("source byte identity drift")

    def test_rejects_malformed_source_shape_record(self) -> None:
        record = self.repository / SOURCE_SHAPE_RECORD
        record.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
        self.assert_rejected("source-shape review record contains a duplicate key")

    def test_record_preserves_the_reviewed_source_identity_contract(self) -> None:
        record = json.loads((REPOSITORY / SOURCE_SHAPE_RECORD).read_text())
        self.assertEqual(record["schema_version"], 2)
        self.assertEqual(
            record["measurement"],
            "python-nonblank-noncomment-lines-v1+ordered-source-byte-identity-v1",
        )
        self.assertEqual(
            record["rebaseline_requirement"], "independent-source-shape-review"
        )
        reviewed_shape = record["reviewed_shape"]
        self.assertEqual(
            reviewed_shape["aggregate_source_lines"],
            sum(reviewed_shape["module_source_lines"].values()),
        )
        self.assertEqual(record["tripwires"]["per_module_source_lines"], 625)
        self.assertEqual(record["tripwires"]["aggregate_source_lines"], 1725)
        source_identity = record["source_byte_identity"]
        self.assertEqual(source_identity["algorithm"], "sha256")
        self.assertEqual(source_identity["framing"], "path-utf8-nul-sha256-hex-nul-v1")
        self.assertEqual(
            [entry["path"] for entry in source_identity["entries"]],
            sorted(reviewed_shape["module_source_lines"]),
        )
        self.assertEqual(len(source_identity["aggregate_sha256"]), 64)
        self.assertEqual(
            record["review_context"]["external_review"]["authenticity_proof"],
            "external-frozen-review-evidence",
        )
        self.assertEqual(
            record["review_context"]["external_review"]["record_role"],
            "source-shape-measurement-not-review-authentication",
        )

    def test_public_contracts_are_release_owned_and_documented(self) -> None:
        validator = runpy.run_path(str(VALIDATOR))
        record = json.loads(
            (REPOSITORY / SOURCE_SHAPE_RECORD).read_text(encoding="utf-8")
        )
        architecture = record["review_context"]["architecture"]
        readme = (REPOSITORY / "README.md").read_text(encoding="utf-8")
        expected_acceptance = (
            "Only a canonical client invocation is acceptable. The client itself "
            "invokes the canonical Task Witness subprocess with deployment-owned "
            "exact `argv` and a scrubbed environment. It accepts the result only if "
            "that child exits with status 0, emits exactly one schema-valid canonical "
            "envelope, and returns exactly the expected complete anchor. The anchor "
            "does not authenticate invocation provenance or arbitrary caller ambient "
            "state."
        )
        expected_executor = (
            "`main()` is Task Witness's only supported subprocess entry point. The "
            "launcher defensively rejects noncanonical CPython warning options, "
            "implementation options, and semantic flags before loading payloads. It "
            "cannot prove the caller's exact argv, environment, cwd, stdin, "
            "inherited descriptors, or timeout; the canonical client and deployment "
            "own those invocation conditions."
        )
        expected_boundary = (
            "Task Witness's filesystem checks operate within a cooperative "
            "same-EUID deployment boundary. They cannot adversarially protect the "
            "launcher, active record, or generation state from an actor with the "
            "same EUID who can replace those files. Deployment policy and external "
            "deployment receipts own that trust; a successful envelope is not a "
            "deployment receipt."
        )

        self.assertEqual(validator["CLIENT_ACCEPTANCE_CONTRACT"], expected_acceptance)
        self.assertEqual(validator["DEPLOYMENT_BOUNDARY_CONTRACT"], expected_boundary)
        self.assertEqual(architecture["client_acceptance"], expected_acceptance)
        self.assertEqual(validator["CANONICAL_EXECUTOR_CONTRACT"], expected_executor)
        self.assertEqual(architecture["canonical_executor"], expected_executor)
        self.assertEqual(architecture["deployment_boundary"], expected_boundary)
        self.assertIn(expected_acceptance, " ".join(readme.split()))
        self.assertIn(expected_executor, " ".join(readme.split()))
        self.assertIn(expected_boundary, " ".join(readme.split()))

    def test_rejects_public_contract_drift(self) -> None:
        record = self.repository / SOURCE_SHAPE_RECORD
        original = record.read_text(encoding="utf-8")
        cases = {
            "client acceptance contract drift": (
                "client_acceptance",
                "Clients may compare only part of the returned anchor.",
            ),
            "deployment boundary drift": (
                "deployment_boundary",
                "Task Witness protects its installation from every same-EUID actor.",
            ),
            "canonical executor contract drift": (
                "canonical_executor",
                "The anchor proves every caller environment.",
            ),
        }
        for expected, (field, replacement) in cases.items():
            with self.subTest(field=field):
                value = json.loads(original)
                value["review_context"]["architecture"][field] = replacement
                record.write_text(json.dumps(value), encoding="utf-8")
                self.assert_rejected(expected)
                record.write_text(original, encoding="utf-8")

    def test_rejects_source_byte_identity_order_drift(self) -> None:
        record = self.repository / SOURCE_SHAPE_RECORD
        value = json.loads(record.read_text(encoding="utf-8"))
        value["source_byte_identity"]["entries"].reverse()
        record.write_text(json.dumps(value), encoding="utf-8")
        self.assert_rejected("ordered source-byte identity drift")

    def test_rejects_source_shape_record_contract_drift(self) -> None:
        record = self.repository / SOURCE_SHAPE_RECORD
        original = record.read_text(encoding="utf-8")
        cases = (
            ("unknown root field", lambda value: value.__setitem__("extra", True)),
            (
                "bad measurement",
                lambda value: value.__setitem__("measurement", "lines-v0"),
            ),
            (
                "missing source byte identity",
                lambda value: value.pop("source_byte_identity"),
            ),
            (
                "missing review context",
                lambda value: value.pop("review_context"),
            ),
            (
                "non-integer schema version",
                lambda value: value.__setitem__("schema_version", 1.0),
            ),
            (
                "bad rebaseline requirement",
                lambda value: value.__setitem__(
                    "rebaseline_requirement", "self-approve"
                ),
            ),
            (
                "path inventory drift",
                lambda value: value["reviewed_shape"]["module_source_lines"].pop(
                    "plugins/task-witness/runtime/trust.py"
                ),
            ),
            (
                "inconsistent aggregate",
                lambda value: value["reviewed_shape"].__setitem__(
                    "aggregate_source_lines", 1
                ),
            ),
            (
                "boolean integer",
                lambda value: value["tripwires"].__setitem__(
                    "per_module_source_lines", True
                ),
            ),
            (
                "reviewed shape above tripwire",
                lambda value: value["tripwires"].__setitem__(
                    "per_module_source_lines", 514
                ),
            ),
        )
        for label, mutate in cases:
            with self.subTest(label=label):
                malformed = json.loads(original)
                mutate(malformed)
                record.write_text(json.dumps(malformed), encoding="utf-8")
                result = self.validate()
                self.assertNotEqual(result.returncode, 0)
                record.write_text(original, encoding="utf-8")

        aggregate = json.loads(original)["reviewed_shape"]["aggregate_source_lines"]
        aggregate_field = f'"aggregate_source_lines": {aggregate}'
        self.assertIn(aggregate_field, original)
        record.write_text(
            original.replace(aggregate_field, '"aggregate_source_lines": 1e999', 1),
            encoding="utf-8",
        )
        self.assert_rejected("source-shape review record contains a non-finite number")

    def test_rejects_source_shape_record_symlink(self) -> None:
        record = self.repository / SOURCE_SHAPE_RECORD
        replacement = self.repository / "source-shape-review.json"
        replacement.write_bytes(record.read_bytes())
        record.unlink()
        record.symlink_to(replacement)
        self.assert_rejected("source-shape review record must be a regular file")

    def test_rejects_runtime_syntax_error(self) -> None:
        runtime = self.plugin / "runtime" / "task_witness.py"
        runtime.write_text("def broken(:\n")
        self.assert_rejected("syntax is invalid")
