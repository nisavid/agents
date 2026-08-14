from __future__ import annotations

import copy
import json
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import validate_versionkeeping

REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPO_ROOT / "scripts" / "validate_versionkeeping.py"
CHECKPOINT_ROOT = Path(
    "plugins/versionkeeping/skills/checkpointing-and-publishing-git-work"
)
EVAL_ROOT = Path("evals/versionkeeping")
CHECKPOINT_EVAL_ROOT = EVAL_ROOT / "skills/checkpointing-and-publishing-git-work"
CONFLICT_ROOT = Path("plugins/versionkeeping/skills/resolving-merge-conflicts")
CONTENT_LOCK = Path("release/plugin-content-locks/versionkeeping.json")
AGENT_PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
CANONICAL_IDENTITY_FIELDS = (
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
)


class ValidateVersionkeepingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name) / "repo"
        shutil.copytree(REPO_ROOT / "plugins", self.repo / "plugins", symlinks=True)
        shutil.copytree(REPO_ROOT / EVAL_ROOT, self.repo / EVAL_ROOT, symlinks=True)
        (self.repo / CONTENT_LOCK).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / CONTENT_LOCK, self.repo / CONTENT_LOCK)
        subprocess.run(
            ["git", "init", "--quiet", str(self.repo)],
            text=True,
            capture_output=True,
            check=True,
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def validate(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VALIDATOR), str(self.repo), *arguments],
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_rejected(self, expected: str) -> None:
        result = self.validate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(expected, result.stderr)

    def test_accepts_current_contract(self) -> None:
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_uses_canonical_agent_plugins_v1_manifest_and_discovery(self) -> None:
        plugin = self.repo / "plugins/versionkeeping"
        canonical = json.loads((plugin / "plugin.json").read_text())
        topology = json.loads((plugin / "topology.json").read_text())

        self.assertEqual(canonical["$schema"], AGENT_PLUGIN_SCHEMA)
        self.assertEqual(canonical["name"], "versionkeeping")
        self.assertEqual(canonical["version"], "1.0.0")
        self.assertEqual(set(canonical["extensions"]), {"com.openai"})
        self.assertEqual(set(canonical["extensions"]["com.openai"]), {"interface"})
        self.assertFalse((plugin / ".codex-plugin").exists())
        self.assertEqual(
            sorted(
                path.name
                for path in (plugin / "skills").iterdir()
                if path.is_dir() and (path / "SKILL.md").is_file()
            ),
            sorted(component["name"] for component in topology["skills"]),
        )

    def test_claude_manifest_is_exact_canonical_projection(self) -> None:
        plugin = self.repo / "plugins/versionkeeping"
        canonical = json.loads((plugin / "plugin.json").read_text())
        claude = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(set(claude), set(CANONICAL_IDENTITY_FIELDS) | {"displayName"})
        self.assertEqual(claude["displayName"], "Versionkeeping")
        self.assertEqual(
            {field: claude[field] for field in CANONICAL_IDENTITY_FIELDS},
            {field: canonical[field] for field in CANONICAL_IDENTITY_FIELDS},
        )

    def test_requires_dirty_direct_worktree_quarantine_and_retention_contract(
        self,
    ) -> None:
        path = self.repo / CHECKPOINT_ROOT / "references/terminal-cleanup.md"
        path.write_text(
            path.read_text().replace(
                "Never run `git worktree remove --force`",
                "Run `git worktree remove --force` after confirmation",
            )
        )
        self.assert_rejected("terminal cleanup contract missing")

    def test_requires_exact_quarantine_registration_repair_contract(self) -> None:
        path = self.repo / CHECKPOINT_ROOT / "references/terminal-cleanup.md"
        path.write_text(
            path.read_text().replace(
                "`git worktree repair <selected-quarantine-path>`",
                "`git worktree list <selected-quarantine-path>`",
            )
        )
        self.assert_rejected("terminal cleanup contract missing")

    def test_exact_worktree_repair_rebinds_quarantined_registration(self) -> None:
        root = Path(self.tempdir.name).resolve()
        repository = root / "repair-main"
        linked = root / "repair-linked"
        quarantine = root / "repair-quarantine"
        subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "-c",
                "user.name=test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "--allow-empty",
                "--quiet",
                "-m",
                "initial",
            ],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "worktree",
                "add",
                "--quiet",
                "-b",
                "topic",
                str(linked),
            ],
            check=True,
        )

        linked.rename(quarantine)
        before = subprocess.run(
            ["git", "-C", str(repository), "worktree", "list", "--porcelain"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        self.assertIn(f"worktree {linked}", before)
        self.assertNotIn(f"worktree {quarantine}", before)

        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "worktree",
                "repair",
                str(quarantine),
            ],
            check=True,
        )
        after = subprocess.run(
            ["git", "-C", str(repository), "worktree", "list", "--porcelain"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        self.assertIn(f"worktree {quarantine}", after)
        self.assertNotIn(f"worktree {linked}", after)
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "show-ref",
                "--verify",
                "refs/heads/topic",
            ],
            check=True,
            capture_output=True,
        )
        metadata_roots = list((repository / ".git/worktrees").iterdir())
        self.assertEqual(len(metadata_roots), 1)
        self.assertEqual(
            (metadata_roots[0] / "gitdir").read_text().strip(),
            str(quarantine / ".git"),
        )

    def test_requires_top_level_ignored_inventory_sentinel_coverage(self) -> None:
        path = self.repo / EVAL_ROOT / "fixtures/discard-reenumeration.md"
        path.write_text(path.read_text().replace(".cache/ignored-sentinel", ""))
        self.assert_rejected("top-level discard eval contract missing")

    def test_requires_fresh_fork_reprobe_semantics(self) -> None:
        path = (
            self.repo
            / "plugins/versionkeeping/skills/syncing-forks-with-upstream/SKILL.md"
        )
        path.write_text(path.read_text().replace("fork_reprobe_sha", "fork_target_sha"))
        self.assert_rejected("fork re-probe verification contract drift")

    def test_rejects_known_negative_trigger_reversed_to_true(self) -> None:
        path = self.repo / CHECKPOINT_EVAL_ROOT / "trigger-evals.json"
        triggers = json.loads(path.read_text())
        next(
            item
            for item in triggers
            if item["query"] == "Draft a birthday invitation in plain text."
        )["should_trigger"] = True
        path.write_text(json.dumps(triggers, indent=2) + "\n")
        self.assert_rejected("trigger eval semantic contract drift")

    def test_rejects_empty_external_behavior_evals(self) -> None:
        path = self.repo / CHECKPOINT_EVAL_ROOT / "evals.json"
        payload = json.loads(path.read_text())
        payload["evals"] = []
        path.write_text(json.dumps(payload, indent=2) + "\n")
        self.assert_rejected("behavior eval contract")

    def test_rejects_executable_plugin_file_mode_drift(self) -> None:
        path = self.repo / CHECKPOINT_ROOT / "scripts/plan_git_publication.py"
        for mode in (0o644, 0o777):
            with self.subTest(mode=oct(mode)):
                path.chmod(mode)
                self.assert_rejected("semantic content lock")
                path.chmod(0o755)

    def test_rejects_pull_request_ownership_reversal(self) -> None:
        path = self.repo / CHECKPOINT_ROOT / "SKILL.md"
        path.write_text(
            path.read_text().replace("pull-request creation", "change-request creation")
        )
        self.assert_rejected("checkpoint ownership boundary")

    def test_rejects_cleanup_provenance_regression(self) -> None:
        path = self.repo / CHECKPOINT_ROOT / "references/terminal-cleanup.md"
        path.write_text(
            path.read_text().replace("Harness-created worktree", "Managed worktree")
        )
        self.assert_rejected("terminal cleanup contract")

    def test_rejects_discard_reenumeration_regression(self) -> None:
        path = self.repo / CHECKPOINT_ROOT / "references/terminal-cleanup.md"
        path.write_text(
            path.read_text().replace(
                "Immediately after confirmation, re-enumerate the confirmed branch, registered\n"
                "worktree, commit, selected quarantine path, destination absence/safety proof,\n"
                "and every bound dirty-path identity before moving anything.",
                "After confirmation, move the worktree.",
            )
        )
        self.assert_rejected("destructive discard semantic contract drift")

    def test_rejects_discard_snapshot_identity_regression(self) -> None:
        path = self.repo / CHECKPOINT_ROOT / "references/terminal-cleanup.md"
        path.write_text(
            path.read_text().replace(
                "and ignored entry that discard or quarantine handling could affect.",
                "and ignored entry that quarantine handling could affect.",
            )
        )
        self.assert_rejected("destructive discard semantic contract drift")

    def test_rejects_same_path_dirty_byte_drift_eval_regression(self) -> None:
        path = self.repo / EVAL_ROOT / "fixtures/discard-reenumeration.md"
        path.write_text(
            path.read_text().replace(
                "same path and status",
                "same path",
            )
        )
        self.assert_rejected("discard re-enumeration eval contract")

    def test_rejects_gitlink_and_head_only_submodule_identity(self) -> None:
        path = self.repo / CHECKPOINT_ROOT / "references/terminal-cleanup.md"
        path.write_text(
            path.read_text().replace(
                "Define each submodule identity recursively as its recorded gitlink, worktree\n"
                "HEAD, and a recursive content-addressed dirty snapshot.",
                "Define each submodule identity as its recorded gitlink and worktree HEAD only.",
            )
        )
        self.assert_rejected("recursive submodule identity contract")

    def test_rejects_phrase_complete_submodule_identity_contradiction(self) -> None:
        path = self.repo / CHECKPOINT_ROOT / "references/terminal-cleanup.md"
        path.write_text(
            path.read_text()
            + "\nRecorded gitlink and worktree HEAD are sufficient; nested dirty bytes may be ignored.\n"
        )
        self.assert_rejected("terminal cleanup contract contradiction")

    def test_write_lock_rejects_differently_worded_contradiction_before_write(
        self,
    ) -> None:
        terminal = self.repo / CHECKPOINT_ROOT / "references/terminal-cleanup.md"
        terminal.write_text(
            terminal.read_text()
            + "\nOnce nested commit identifiers agree, changes beneath that checkout do not require renewed approval.\n"
        )
        lock = self.repo / CONTENT_LOCK
        original_lock = lock.read_bytes()

        result = self.validate("--write-content-lock")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("destructive discard semantic contract drift", result.stderr)
        self.assertEqual(lock.read_bytes(), original_lock)

    def test_write_lock_rejects_drift_before_atomic_replacement(self) -> None:
        terminal = self.repo / CHECKPOINT_ROOT / "references/terminal-cleanup.md"
        corpus = self.repo / EVAL_ROOT / "corpus.json"
        lock = self.repo / CONTENT_LOCK
        original_lock = lock.read_bytes()
        original_check = (
            validate_versionkeeping.require_content_lock_write_snapshot_unchanged
        )
        checks = 0

        def mutate_before_precommit_check(
            repo_root: Path,
            snapshot: validate_versionkeeping.ContentLockWriteSnapshot,
        ) -> None:
            nonlocal checks
            checks += 1
            if checks == 2:
                terminal.write_text(terminal.read_text() + "\nConcurrent drift.\n")
                payload = json.loads(corpus.read_text())
                scenario = next(
                    item
                    for item in payload["scenarios"]
                    if item["id"] == "discard-reenumeration"
                )
                scenario["prompt"] = "Concurrent discard scenario drift."
                corpus.write_text(json.dumps(payload, indent=2) + "\n")
            original_check(repo_root, snapshot)

        with (
            mock.patch.object(
                validate_versionkeeping,
                "require_content_lock_write_snapshot_unchanged",
                side_effect=mutate_before_precommit_check,
            ),
            mock.patch.object(
                sys,
                "argv",
                [str(VALIDATOR), str(self.repo), "--write-content-lock"],
            ),
        ):
            result = validate_versionkeeping.main()

        self.assertEqual(checks, 2)
        self.assertEqual(result, 1)
        self.assertEqual(lock.read_bytes(), original_lock)

    def test_write_lock_discards_recovery_on_drift_after_preservation_before_replace(
        self,
    ) -> None:
        skill = self.repo / CONFLICT_ROOT / "SKILL.md"
        skill.write_text(
            skill.read_text().replace(
                "The recipient rereads live state",
                "The receiving owner rereads live state",
            )
        )
        terminal = self.repo / CHECKPOINT_ROOT / "references/terminal-cleanup.md"
        corpus = self.repo / EVAL_ROOT / "corpus.json"
        lock = self.repo / CONTENT_LOCK
        original_lock = lock.read_bytes()
        original_mode = stat.S_IMODE(lock.stat().st_mode)
        original_check = (
            validate_versionkeeping.require_content_lock_write_snapshot_unchanged
        )
        checks = 0

        def mutate_after_recovery_preservation(
            repo_root: Path,
            snapshot: validate_versionkeeping.ContentLockWriteSnapshot,
        ) -> None:
            nonlocal checks
            checks += 1
            if checks == 3:
                terminal.write_text(terminal.read_text() + "\nConcurrent drift.\n")
                payload = json.loads(corpus.read_text())
                scenario = next(
                    item
                    for item in payload["scenarios"]
                    if item["id"] == "discard-reenumeration"
                )
                scenario["prompt"] = "Concurrent discard scenario drift."
                corpus.write_text(json.dumps(payload, indent=2) + "\n")
            original_check(repo_root, snapshot)

        with (
            mock.patch.object(
                validate_versionkeeping,
                "require_content_lock_write_snapshot_unchanged",
                side_effect=mutate_after_recovery_preservation,
            ),
            mock.patch.object(validate_versionkeeping.os, "replace") as replace,
            mock.patch.object(
                sys,
                "argv",
                [str(VALIDATOR), str(self.repo), "--write-content-lock"],
            ),
        ):
            result = validate_versionkeeping.main()

        self.assertEqual(checks, 3)
        self.assertEqual(result, 1)
        replace.assert_not_called()
        self.assertEqual(lock.read_bytes(), original_lock)
        self.assertEqual(stat.S_IMODE(lock.stat().st_mode), original_mode)
        self.assertEqual(
            list(
                validate_versionkeeping.content_lock_recovery_root(self.repo).glob("*")
            ),
            [],
        )

    def test_write_lock_preserves_prior_lock_recovery_after_post_replace_drift(
        self,
    ) -> None:
        skill = self.repo / CONFLICT_ROOT / "SKILL.md"
        skill.write_text(
            skill.read_text().replace(
                "The recipient rereads live state",
                "The receiving owner rereads live state",
            )
        )
        terminal = self.repo / CHECKPOINT_ROOT / "references/terminal-cleanup.md"
        corpus = self.repo / EVAL_ROOT / "corpus.json"
        lock = self.repo / CONTENT_LOCK
        lock.chmod(0o640)
        original_lock = lock.read_bytes()
        original_mode = stat.S_IMODE(lock.stat().st_mode)
        original_check = validate_versionkeeping.require_content_lock_write_snapshot_after_replacement
        replacement_observed = False

        def mutate_after_replacement(
            repo_root: Path,
            snapshot: validate_versionkeeping.ContentLockWriteSnapshot,
            content: bytes,
            mode: int,
        ) -> None:
            nonlocal replacement_observed
            replacement_observed = lock.read_bytes() != original_lock
            terminal.write_text(terminal.read_text() + "\nConcurrent drift.\n")
            payload = json.loads(corpus.read_text())
            scenario = next(
                item
                for item in payload["scenarios"]
                if item["id"] == "discard-reenumeration"
            )
            scenario["prompt"] = "Concurrent discard scenario drift."
            corpus.write_text(json.dumps(payload, indent=2) + "\n")
            original_check(repo_root, snapshot, content, mode)

        with (
            mock.patch.object(
                validate_versionkeeping,
                "require_content_lock_write_snapshot_after_replacement",
                side_effect=mutate_after_replacement,
            ),
            mock.patch.object(
                sys,
                "argv",
                [str(VALIDATOR), str(self.repo), "--write-content-lock"],
            ),
        ):
            result = validate_versionkeeping.main()

        self.assertTrue(replacement_observed)
        self.assertEqual(result, 1)
        self.assertNotEqual(lock.read_bytes(), original_lock)
        self.assertEqual(stat.S_IMODE(lock.stat().st_mode), original_mode)
        artifacts = list(
            validate_versionkeeping.content_lock_recovery_root(self.repo).glob("*")
        )
        self.assertEqual(len(artifacts), 1)
        self.assertEqual((artifacts[0] / "prior-lock.bin").read_bytes(), original_lock)
        manifest = json.loads((artifacts[0] / "manifest.json").read_text())
        self.assertEqual(manifest["prior_lock"]["mode"], original_mode)
        self.assertEqual(manifest["rerun"], "quiescent rerun required")

    def test_write_lock_retains_prior_recovery_when_replace_then_raises(
        self,
    ) -> None:
        skill = self.repo / CONFLICT_ROOT / "SKILL.md"
        skill.write_text(
            skill.read_text().replace(
                "The recipient rereads live state",
                "The receiving owner rereads live state",
            )
        )
        lock = self.repo / CONTENT_LOCK
        lock.chmod(0o640)
        original_lock = lock.read_bytes()
        original_mode = stat.S_IMODE(lock.stat().st_mode)
        original_replace = validate_versionkeeping.os.replace
        replace_calls = 0

        def replace_then_raise(source: str | Path, destination: str | Path) -> None:
            nonlocal replace_calls
            replace_calls += 1
            original_replace(source, destination)
            raise OSError("replacement result is ambiguous")

        with (
            mock.patch.object(
                validate_versionkeeping.os,
                "replace",
                side_effect=replace_then_raise,
            ),
            mock.patch.object(
                sys,
                "argv",
                [str(VALIDATOR), str(self.repo), "--write-content-lock"],
            ),
        ):
            result = validate_versionkeeping.main()

        self.assertEqual(result, 1)
        self.assertEqual(replace_calls, 1)
        self.assertNotEqual(lock.read_bytes(), original_lock)
        self.assertEqual(stat.S_IMODE(lock.stat().st_mode), original_mode)
        artifacts = list(
            validate_versionkeeping.content_lock_recovery_root(self.repo).glob("*")
        )
        self.assertEqual(len(artifacts), 1)
        self.assertEqual((artifacts[0] / "prior-lock.bin").read_bytes(), original_lock)
        self.assertEqual(
            stat.S_IMODE((artifacts[0] / "prior-lock.bin").stat().st_mode),
            original_mode,
        )

    def test_write_lock_preserves_prior_absence_recovery_after_post_replace_drift(
        self,
    ) -> None:
        skill = self.repo / CONFLICT_ROOT / "SKILL.md"
        skill.write_text(
            skill.read_text().replace(
                "The recipient rereads live state",
                "The receiving owner rereads live state",
            )
        )
        terminal = self.repo / CHECKPOINT_ROOT / "references/terminal-cleanup.md"
        corpus = self.repo / EVAL_ROOT / "corpus.json"
        lock = self.repo / CONTENT_LOCK
        lock.unlink()
        original_check = validate_versionkeeping.require_content_lock_write_snapshot_after_replacement
        replacement_observed = False

        def mutate_after_replacement(
            repo_root: Path,
            snapshot: validate_versionkeeping.ContentLockWriteSnapshot,
            content: bytes,
            mode: int,
        ) -> None:
            nonlocal replacement_observed
            replacement_observed = lock.exists()
            terminal.write_text(terminal.read_text() + "\nConcurrent drift.\n")
            payload = json.loads(corpus.read_text())
            scenario = next(
                item
                for item in payload["scenarios"]
                if item["id"] == "discard-reenumeration"
            )
            scenario["prompt"] = "Concurrent discard scenario drift."
            corpus.write_text(json.dumps(payload, indent=2) + "\n")
            original_check(repo_root, snapshot, content, mode)

        with (
            mock.patch.object(
                validate_versionkeeping,
                "require_content_lock_write_snapshot_after_replacement",
                side_effect=mutate_after_replacement,
            ),
            mock.patch.object(
                sys,
                "argv",
                [str(VALIDATOR), str(self.repo), "--write-content-lock"],
            ),
        ):
            result = validate_versionkeeping.main()

        self.assertTrue(replacement_observed)
        self.assertEqual(result, 1)
        self.assertTrue(lock.exists())
        artifacts = list(
            validate_versionkeeping.content_lock_recovery_root(self.repo).glob("*")
        )
        self.assertEqual(len(artifacts), 1)
        self.assertFalse((artifacts[0] / "prior-lock.bin").exists())
        manifest = json.loads((artifacts[0] / "manifest.json").read_text())
        self.assertEqual(manifest["prior_lock"]["kind"], "missing")

    def test_write_lock_preserves_concurrent_replacement_after_post_replace_drift(
        self,
    ) -> None:
        skill = self.repo / CONFLICT_ROOT / "SKILL.md"
        skill.write_text(
            skill.read_text().replace(
                "The recipient rereads live state",
                "The receiving owner rereads live state",
            )
        )
        terminal = self.repo / CHECKPOINT_ROOT / "references/terminal-cleanup.md"
        lock = self.repo / CONTENT_LOCK
        concurrent_content = b'{"concurrent": "writer"}\n'
        original_check = validate_versionkeeping.require_content_lock_write_snapshot_after_replacement

        def replace_lock_after_replacement(
            repo_root: Path,
            snapshot: validate_versionkeeping.ContentLockWriteSnapshot,
            content: bytes,
            mode: int,
        ) -> None:
            lock.write_bytes(concurrent_content)
            terminal.write_text(terminal.read_text() + "\nConcurrent drift.\n")
            original_check(repo_root, snapshot, content, mode)

        with (
            mock.patch.object(
                validate_versionkeeping,
                "require_content_lock_write_snapshot_after_replacement",
                side_effect=replace_lock_after_replacement,
            ),
            mock.patch.object(
                sys,
                "argv",
                [str(VALIDATOR), str(self.repo), "--write-content-lock"],
            ),
        ):
            result = validate_versionkeeping.main()

        self.assertEqual(result, 1)
        self.assertEqual(lock.read_bytes(), concurrent_content)
        artifacts = list(
            validate_versionkeeping.content_lock_recovery_root(self.repo).glob("*")
        )
        self.assertEqual(len(artifacts), 1)

    def test_write_lock_fails_before_replacement_when_prior_recovery_cannot_be_saved(
        self,
    ) -> None:
        lock = self.repo / CONTENT_LOCK
        original_lock = lock.read_bytes()

        with (
            mock.patch.object(
                validate_versionkeeping,
                "preserve_prior_content_lock_recovery",
                side_effect=validate_versionkeeping.ContractError(
                    "recovery unavailable"
                ),
            ),
            mock.patch.object(
                sys,
                "argv",
                [str(VALIDATOR), str(self.repo), "--write-content-lock"],
            ),
        ):
            result = validate_versionkeeping.main()

        self.assertEqual(result, 1)
        self.assertEqual(lock.read_bytes(), original_lock)

    def test_write_lock_rejects_symlinked_private_recovery_storage_before_replace(
        self,
    ) -> None:
        skill = self.repo / CONFLICT_ROOT / "SKILL.md"
        skill.write_text(
            skill.read_text().replace(
                "The recipient rereads live state",
                "The receiving owner rereads live state",
            )
        )
        lock = self.repo / CONTENT_LOCK
        original_lock = lock.read_bytes()
        recovery_root = validate_versionkeeping.content_lock_recovery_root(self.repo)
        target = self.repo / "private-recovery-target"
        target.mkdir(mode=0o700)
        recovery_root.parent.symlink_to(target, target_is_directory=True)

        with mock.patch.object(
            sys,
            "argv",
            [str(VALIDATOR), str(self.repo), "--write-content-lock"],
        ):
            result = validate_versionkeeping.main()

        self.assertEqual(result, 1)
        self.assertEqual(lock.read_bytes(), original_lock)

    def test_content_lock_capture_rejects_concurrent_mode_bytes_and_path_changes(
        self,
    ) -> None:
        path = self.repo / CHECKPOINT_ROOT / "scripts/plan_git_publication.py"
        original_fstat = validate_versionkeeping.os.fstat

        for mutation in (
            lambda: path.chmod(0o644),
            lambda: path.write_bytes(path.read_bytes() + b"# drift\n"),
            lambda: path.replace(path.with_suffix(".replacement")),
        ):
            with self.subTest(mutation=mutation):
                shutil.copy2(
                    REPO_ROOT / CHECKPOINT_ROOT / "scripts/plan_git_publication.py",
                    path,
                )
                calls = 0

                def mutate_after_read(descriptor: int):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        mutation()
                    return original_fstat(descriptor)

                with mock.patch.object(
                    validate_versionkeeping.os,
                    "fstat",
                    side_effect=mutate_after_read,
                ):
                    with self.assertRaisesRegex(
                        validate_versionkeeping.ContractError,
                        "validated input changed while snapshotting",
                    ):
                        validate_versionkeeping.capture_input_entry(path)

    def test_rejects_eval_seven_forced_dirty_worktree_removal_oracle_before_digest(
        self,
    ) -> None:
        path = self.repo / CHECKPOINT_EVAL_ROOT / "evals.json"
        payload = json.loads(path.read_text())
        evaluation = next(item for item in payload["evals"] if item["id"] == 7)
        evaluation["expectations"][-2]["text"] = (
            "Permits git worktree remove --force after exact discard confirmation."
        )
        path.write_text(json.dumps(payload, indent=2) + "\n")
        self.assert_rejected("behavior eval seven safety oracle drift")

    def test_rejects_contradictory_discard_fixture_prose(self) -> None:
        fixture = self.repo / EVAL_ROOT / "fixtures/discard-reenumeration.md"
        fixture.write_text(
            fixture.read_text()
            + "\nNested byte drift leaves the old approval usable when enclosing commit identifiers are unchanged.\n"
        )
        self.assert_rejected("discard fixture semantic contract drift")

    def test_rejects_corpus_prompt_directing_immediate_stale_discard(self) -> None:
        path = self.repo / EVAL_ROOT / "corpus.json"
        corpus = json.loads(path.read_text())
        scenario = next(
            item
            for item in corpus["scenarios"]
            if item["id"] == "discard-reenumeration"
        )
        scenario["prompt"] = (
            "Use the stale confirmation and remove the dirty worktree immediately."
        )
        path.write_text(json.dumps(corpus, indent=2) + "\n")
        self.assert_rejected("discard eval scenario semantic contract drift")

    def test_rejects_same_head_nested_submodule_byte_drift_eval_regression(
        self,
    ) -> None:
        path = self.repo / EVAL_ROOT / "fixtures/discard-reenumeration.md"
        path.write_text(
            path.read_text().replace(
                "same nested path and status",
                "same nested status",
            )
        )
        self.assert_rejected("submodule discard eval contract")

    def test_rejects_unbounded_discard_snapshot_contract(self) -> None:
        path = self.repo / CHECKPOINT_ROOT / "references/terminal-cleanup.md"
        original = path.read_text()
        mutations = (
            ("at most 100,000 entries", "an unlimited number of entries"),
            ("At most three complete attempts", "Retry until the snapshot stabilizes"),
        )
        for old, new in mutations:
            with self.subTest(old=old):
                path.write_text(original.replace(old, new))
                self.assert_rejected("destructive discard semantic contract drift")

    def test_rejects_inventory_budget_fixture_allowing_partial_snapshot(self) -> None:
        path = self.repo / EVAL_ROOT / "fixtures/discard-inventory-budget-exceeded.md"
        path.write_text(
            path.read_text().replace(
                "identity is unknown\nand cleanup is blocked",
                "the available inventory is close enough and cleanup may continue",
            )
        )
        self.assert_rejected("discard budget fixture semantic contract drift")

    def test_rejects_content_budget_fixture_allowing_partial_hash(self) -> None:
        path = self.repo / EVAL_ROOT / "fixtures/discard-content-budget-exceeded.md"
        path.write_text(
            path.read_text().replace(
                "identity is unknown and cleanup\nis blocked",
                "the observed prefix is sufficient and cleanup may continue",
            )
        )
        self.assert_rejected("discard budget fixture semantic contract drift")

    def test_rejects_reviewed_plan_digest_regression(self) -> None:
        path = self.repo / CHECKPOINT_ROOT / "references/publication-execution.md"
        path.write_text(
            path.read_text().replace(
                "--reviewed-plan-sha256", "--untrusted-plan-sha256"
            )
        )
        self.assert_rejected("checkpoint safety contract")

    def test_rejects_remote_ref_deletion_authorization_regression(self) -> None:
        path = self.repo / CHECKPOINT_ROOT / "references/terminal-cleanup.md"
        path.write_text(
            path.read_text().replace(
                "explicit repository and operator authorization",
                "generic authorization",
            )
        )
        self.assert_rejected("terminal remote deletion contract")

    def test_rejects_fork_default_identity_regression(self) -> None:
        path = (
            self.repo
            / "plugins/versionkeeping/skills/syncing-forks-with-upstream/SKILL.md"
        )
        path.write_text(path.read_text() + "\nRun `git fetch origin upstream`.\n")
        self.assert_rejected("fork contract contains default literal")

    def test_rejects_fork_direct_local_integration(self) -> None:
        path = (
            self.repo
            / "plugins/versionkeeping/skills/syncing-forks-with-upstream/SKILL.md"
        )
        path.write_text(
            path.read_text()
            + "\n```sh\n"
            + 'git merge --no-ff "$upstream_sha"\n'
            + "```\n"
        )
        self.assert_rejected("fork contract performs direct local Git integration")

    def test_rejects_topology_ownership_drift(self) -> None:
        path = self.repo / "plugins/versionkeeping/topology.json"
        topology = json.loads(path.read_text())
        topology["ownership"]["excluded"].remove("Graphite operations")
        path.write_text(json.dumps(topology, indent=2) + "\n")
        self.assert_rejected("topology excluded operations drift")

    def test_rejects_topology_call_or_operation_owner_drift(self) -> None:
        path = self.repo / "plugins/versionkeeping/topology.json"
        topology = json.loads(path.read_text())
        topology["skills"][0]["calls"] = ["syncing-forks-with-upstream"]
        path.write_text(json.dumps(topology, indent=2) + "\n")
        self.assert_rejected("call graph drift")

        shutil.copy2(REPO_ROOT / "plugins/versionkeeping/topology.json", path)
        topology = json.loads(path.read_text())
        topology["operation_owners"]["local-git-integration"] = (
            "syncing-forks-with-upstream"
        )
        path.write_text(json.dumps(topology, indent=2) + "\n")
        self.assert_rejected("operation owner drift")

    def test_rejects_boolean_topology_schema_version(self) -> None:
        path = self.repo / "plugins/versionkeeping/topology.json"
        topology = json.loads(path.read_text())
        topology["schema_version"] = True
        path.write_text(json.dumps(topology, indent=2) + "\n")
        self.assert_rejected("topology identity drift")

    def test_rejects_missing_non_default_fork_fixture(self) -> None:
        path = self.repo / EVAL_ROOT / "fixtures/non-default-fork-sync.md"
        path.unlink()
        self.assert_rejected("required regular file is missing")

    def test_rejects_missing_reverse_edge_scenario(self) -> None:
        path = self.repo / EVAL_ROOT / "corpus.json"
        corpus = json.loads(path.read_text())
        corpus["scenarios"] = [
            item
            for item in corpus["scenarios"]
            if item["id"] != "ownership-reverse-edges"
        ]
        path.write_text(json.dumps(corpus, indent=2) + "\n")
        self.assert_rejected("eval corpus scenario count drift")

    def test_rejects_boolean_and_float_eval_corpus_versions(self) -> None:
        path = self.repo / EVAL_ROOT / "corpus.json"
        original = json.loads(path.read_text())
        for malformed in (True, 1.0):
            with self.subTest(malformed=malformed):
                corpus = json.loads(json.dumps(original))
                corpus["version"] = malformed
                path.write_text(json.dumps(corpus, indent=2) + "\n")
                self.assert_rejected("eval corpus version drift")

    def test_rejects_machine_local_portability_leak(self) -> None:
        path = self.repo / "plugins/versionkeeping/README.md"
        path.write_text(
            path.read_text() + "\nInstall from /Users/ivan/private/plugin.\n"
        )
        self.assert_rejected("machine-local portability leak")

    def test_rejects_machine_local_portability_leak_in_corpus(self) -> None:
        path = self.repo / EVAL_ROOT / "corpus.json"
        corpus = json.loads(path.read_text())
        corpus["scenarios"][0]["prompt"] += " Install from /Users/ivan/private."
        path.write_text(json.dumps(corpus, indent=2) + "\n")
        self.assert_rejected("machine-local portability leak")

    def test_rejects_unexpected_plugin_root_file(self) -> None:
        (self.repo / "plugins/versionkeeping/extra.md").write_text("unexpected\n")
        self.assert_rejected("plugin root file inventory drift")

    def test_rejects_adapter_prompt_without_namespace(self) -> None:
        path = self.repo / "plugins/versionkeeping/plugin.json"
        manifest = json.loads(path.read_text())
        manifest["extensions"]["com.openai"]["interface"]["defaultPrompt"][0] = (
            "Use $checkpointing-and-publishing-git-work."
        )
        path.write_text(json.dumps(manifest, indent=2) + "\n")
        self.assert_rejected("Codex default prompts")

    def test_rejects_duplicate_key_in_trigger_corpus(self) -> None:
        path = self.repo / CHECKPOINT_EVAL_ROOT / "trigger-evals.json"
        path.write_text(
            path.read_text().replace(
                '"should_trigger": true',
                '"should_trigger": true, "should_trigger": false',
                1,
            )
        )
        self.assert_rejected("duplicate JSON key")

    def test_rejects_non_finite_json_in_any_contract(self) -> None:
        path = self.repo / CHECKPOINT_EVAL_ROOT / "trigger-evals.json"
        path.write_text(path.read_text().replace("[", '[{"probe": NaN},', 1))
        self.assert_rejected("non-finite JSON value")

    def test_rejects_semantic_safety_drift_that_preserves_keywords(self) -> None:
        path = self.repo / CHECKPOINT_ROOT / "SKILL.md"
        path.write_text(
            path.read_text().replace(
                "Read-only Git tasks never mutate or publish",
                "Read-only Git tasks may mutate or publish",
            )
        )
        self.assert_rejected("semantic content lock mismatch")

    def test_conflict_resolver_preserves_git_mechanics_ownership(self) -> None:
        topology = json.loads(
            (self.repo / "plugins/versionkeeping/topology.json").read_text()
        )
        resolver = next(
            item
            for item in topology["skills"]
            if item["name"] == "resolving-merge-conflicts"
        )
        self.assertEqual(resolver["calls"], ["checkpointing-and-publishing-git-work"])
        self.assertEqual(resolver["operations"], ["conflict-resolution"])
        self.assertEqual(
            topology["operation_owners"]["conflict-resolution"],
            "resolving-merge-conflicts",
        )
        self.assertEqual(topology["schema_version"], 2)
        self.assertEqual(
            topology["terminal_handoff"],
            {
                "target": "checkpointing-and-publishing-git-work",
                "resolver_owns": [
                    "conflict-interpretation",
                    "authorized-file-edits",
                ],
                "checkpointing_owns": [
                    "stage",
                    "continue",
                    "commit",
                    "push",
                    "abort",
                ],
                "resolver_forbidden": [
                    "stage",
                    "continue",
                    "commit",
                    "push",
                    "abort",
                ],
            },
        )
        skill = (self.repo / CONFLICT_ROOT / "SKILL.md").read_text()
        for required in (
            "conflict interpretation and authorized file edits",
            "stage, continue, commit, and push",
            "authorized abort",
            "never commits its own resolution",
        ):
            self.assertIn(required, skill)

    def test_rejects_every_terminal_handoff_contract_drift(self) -> None:
        path = self.repo / "plugins/versionkeeping/topology.json"
        original = json.loads(path.read_text())
        cases = [("top-level omission", lambda value: value.pop("terminal_handoff"))]
        for field in original.get("terminal_handoff", {}):
            cases.append(
                (
                    f"missing {field}",
                    lambda value, field=field: value["terminal_handoff"].pop(field),
                )
            )
        cases.append(
            (
                "target",
                lambda value: value["terminal_handoff"].update(
                    {"target": "resolving-merge-conflicts"}
                ),
            )
        )
        for field in (
            "resolver_owns",
            "checkpointing_owns",
            "resolver_forbidden",
        ):
            for item in original.get("terminal_handoff", {}).get(field, []):
                cases.append(
                    (
                        f"{field} without {item}",
                        lambda value, field=field, item=item: value["terminal_handoff"][
                            field
                        ].remove(item),
                    )
                )

        for label, mutate in cases:
            with self.subTest(label=label):
                topology = copy.deepcopy(original)
                mutate(topology)
                path.write_text(json.dumps(topology, indent=2) + "\n")
                self.assert_rejected("terminal handoff metadata drift")

    def test_benign_terminal_handoff_prose_can_refresh_its_semantic_lock(self) -> None:
        skill = self.repo / CONFLICT_ROOT / "SKILL.md"
        skill.write_text(
            skill.read_text().replace(
                "The recipient rereads live state",
                "The receiving owner rereads live state",
            )
        )
        result = subprocess.run(
            [
                sys.executable,
                str(VALIDATOR),
                str(self.repo),
                "--write-content-lock",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.validate().returncode, 0)
        self.assertEqual(
            list(
                validate_versionkeeping.content_lock_recovery_root(self.repo).glob("*")
            ),
            [],
        )

    def test_rejects_conflict_resolver_self_commit_regression(self) -> None:
        skill = self.repo / CONFLICT_ROOT / "SKILL.md"
        skill.write_text(
            skill.read_text().replace(
                "never commits its own resolution",
                "commits its own resolution",
            )
        )
        self.assert_rejected("conflict resolver ownership boundary")


if __name__ == "__main__":
    unittest.main()
