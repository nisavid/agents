from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import phase7_compatibility_projection as projection  # noqa: E402


SOURCE_PATHS = (
    Path("plugins/rolecasting/topology.json"),
    Path("plugins/versionkeeping/topology.json"),
    Path("plugins/tricritical/topology.json"),
    Path(
        "plugins/mergecraft/skills/writing-reviewable-pr-descriptions/"
        "references/review-atlas-extension.json"
    ),
    Path("release/mergecraft/review-atlas-contract.json"),
)
FIXTURE_PATH = REPO_ROOT / "tests/fixtures/phase7-v4-compatibility.json"
SCRIPT_PATH = REPO_ROOT / "scripts/phase7_compatibility_projection.py"


class Phase7CompatibilityProjectionTests(unittest.TestCase):
    def candidate_copy(self, root: Path) -> Path:
        subprocess.run(["git", "init", "--quiet", str(root)], check=True)
        for relative in SOURCE_PATHS:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO_ROOT / relative, destination)
        return root

    def load(self, root: Path, relative: Path) -> dict[str, object]:
        return json.loads((root / relative).read_text(encoding="utf-8"))

    def write(
        self, root: Path, relative: Path, document: dict[str, object]
    ) -> None:
        (root / relative).write_text(
            json.dumps(document, indent=2) + "\n", encoding="utf-8"
        )

    def test_projection_is_byte_identical_to_frozen_v4_fixture(self) -> None:
        self.assertEqual(
            projection.compatibility_bytes(REPO_ROOT),
            FIXTURE_PATH.read_bytes(),
        )

    def test_isolated_cli_emits_only_exact_compatibility_bytes(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                str(SCRIPT_PATH),
                "--public-root",
                str(REPO_ROOT),
                "--expected-public-candidate-sha256",
                projection.candidate_content_identity(
                    REPO_ROOT, error_factory=projection.CompatibilityProjectionError
                ),
            ],
            check=False,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, FIXTURE_PATH.read_bytes())
        self.assertEqual(result.stderr, b"")

    def test_isolated_cli_fails_closed_when_a_source_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.candidate_copy(Path(directory))
            identity = projection.candidate_content_identity(
                root, error_factory=projection.CompatibilityProjectionError
            )
            (root / SOURCE_PATHS[0]).unlink()
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-B",
                    str(SCRIPT_PATH),
                    "--public-root",
                    str(root),
                    "--expected-public-candidate-sha256",
                    identity,
                ],
                check=False,
                capture_output=True,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"ERROR:", result.stderr)

    def test_isolated_cli_rejects_an_unexpected_candidate_identity(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                str(SCRIPT_PATH),
                "--public-root",
                str(REPO_ROOT),
                "--expected-public-candidate-sha256",
                "0" * 64,
            ],
            check=False,
            capture_output=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"public candidate identity mismatch", result.stderr)

    def test_unconsumed_public_changes_do_not_change_compatibility(self) -> None:
        expected = projection.compatibility_bytes(REPO_ROOT)
        with tempfile.TemporaryDirectory() as directory:
            root = self.candidate_copy(Path(directory))

            rolecasting = self.load(root, SOURCE_PATHS[0])
            rolecasting["unconsumed_addition"] = {"value": "neutral"}
            self.write(root, SOURCE_PATHS[0], rolecasting)

            versionkeeping = self.load(root, SOURCE_PATHS[1])
            versionkeeping["ownership"] = {"changed": "neutral"}
            self.write(root, SOURCE_PATHS[1], versionkeeping)

            tricritical = self.load(root, SOURCE_PATHS[2])
            tricritical["skills"]["review"]["role"] = "neutral-role"
            tricritical["skills"]["new-skill"] = {
                "calls": ["review"],
                "mutates_directly": True,
                "can_cause_mutation": True,
                "requires_original_mutation_authority": True,
                "requires": ["neutral"],
            }
            self.write(root, SOURCE_PATHS[2], tricritical)

            extension = self.load(root, SOURCE_PATHS[3])
            extension["default_overlay_path"] = "/neutral/path"
            extension["absence"] = "neutral-absence"
            extension["file_requirement"] = "neutral-requirement"
            self.write(root, SOURCE_PATHS[3], extension)

            atlas = self.load(root, SOURCE_PATHS[4])
            atlas["prose_sha256"] = {"neutral": "neutral"}
            atlas["visual_budgets"] = {"neutral": "neutral"}
            self.write(root, SOURCE_PATHS[4], atlas)

            (root / "README.md").write_text("neutral prose\n", encoding="utf-8")
            (root / "evals").mkdir()
            (root / "evals/neutral.json").write_text("{}\n", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests/test_neutral.py").write_text(
                "def test_neutral(): pass\n", encoding="utf-8"
            )

            self.assertEqual(projection.compatibility_bytes(root), expected)

    def test_each_projected_family_changes_compatibility(self) -> None:
        mutations = {
            "rolecasting receipt ownership": (
                SOURCE_PATHS[0],
                lambda document: document["receipt_contract"].__setitem__(
                    "owner", "changed-owner"
                ),
            ),
            "versionkeeping operation ownership": (
                SOURCE_PATHS[1],
                lambda document: document["operation_owners"].__setitem__(
                    "git-ref-push", "changed-owner"
                ),
            ),
            "versionkeeping terminal handoff": (
                SOURCE_PATHS[1],
                lambda document: document["terminal_handoff"].__setitem__(
                    "target", "changed-target"
                ),
            ),
            "tricritical call edge": (
                SOURCE_PATHS[2],
                lambda document: document["skills"]["review"].__setitem__(
                    "calls", ["runtime"]
                ),
            ),
            "tricritical mutation authority": (
                SOURCE_PATHS[2],
                lambda document: document["skills"]["revise"].__setitem__(
                    "mutates_directly", False
                ),
            ),
            "tricritical receipt requirement": (
                SOURCE_PATHS[2],
                lambda document: document["skills"]["review"].__setitem__(
                    "requires", []
                ),
            ),
            "review atlas overlay authority": (
                SOURCE_PATHS[3],
                lambda document: document.__setitem__(
                    "precedence", "changed-precedence"
                ),
            ),
            "review atlas release/runtime ownership": (
                SOURCE_PATHS[4],
                lambda document: document["firewall"].__setitem__(
                    "atlas_implementation", "changed-location"
                ),
            ),
        }
        expected = projection.compatibility_bytes(REPO_ROOT)
        for label, (relative, mutate) in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = self.candidate_copy(Path(directory))
                document = copy.deepcopy(self.load(root, relative))
                mutate(document)
                self.write(root, relative, document)
                self.assertNotEqual(
                    projection.compatibility_bytes(root),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
