#!/usr/bin/env python3
"""Tests for PatchState, commit_changes rollback, and cmd_verify.

Run: python3 test_workflow.py
"""

import io
import os
import plistlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import importlib.util
from importlib.machinery import SourceFileLoader

_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chatgpt-ffs")
_loader = SourceFileLoader("codex_patch_manager", _script)
_spec = importlib.util.spec_from_loader("codex_patch_manager", _loader)
cpm = importlib.util.module_from_spec(_spec)
_loader.exec_module(cpm)


# ─── Test Fixtures ──────────────────────────────────────────────────────────


class StubBundle(cpm.AppBundle):
    """AppBundle with subprocess-dependent methods stubbed out.

    Overrides quit, _resign, and verify_signature so tests can exercise
    the workflow logic (commit_changes, cmd_verify) without codesign or
    killall.  Inherits file-I/O methods (ensure_backup, install_asar,
    _update_plist_hash) unchanged.
    """

    def __init__(self, app_path, verify_returns=None):
        super().__init__(app_path)
        if verify_returns is None:
            verify_returns = (True, "valid")
        if isinstance(verify_returns, list):
            self._verify_mock = Mock(side_effect=verify_returns)
        else:
            self._verify_mock = Mock(return_value=verify_returns)

    def quit(self):
        pass

    def _resign(self, progress_cb=None):
        pass

    def verify_signature(self):
        return self._verify_mock()


def _make_test_app(tmpdir, files=None):
    """Create a minimal fake .app bundle for testing.

    Args:
        tmpdir: temp directory root.
        files: dict of {relpath: content} for files to pack into app.asar.
               If None, uses a default file with a gate call.

    Returns:
        (app_path, asar_path, orig_hash) tuple.
    """
    if files is None:
        files = {
            "webview/assets/test-module-abc123.js": "var x = checkGate(`505458`);",
        }

    app_path = os.path.join(tmpdir, "TestApp.app")
    resources = os.path.join(app_path, "Contents", "Resources")
    frameworks = os.path.join(app_path, "Contents", "Frameworks")
    os.makedirs(resources, exist_ok=True)
    os.makedirs(frameworks, exist_ok=True)

    # Create Info.plist via plistlib so _update_plist_hash can load it.
    plist_path = os.path.join(app_path, "Contents", "Info.plist")
    with open(plist_path, "wb") as f:
        plistlib.dump({}, f)

    # Pack the synthetic source files into an asar.
    src_dir = os.path.join(tmpdir, "src")
    os.makedirs(src_dir)
    for relpath, content in files.items():
        full = os.path.join(src_dir, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(content if isinstance(content, bytes) else content.encode())

    asar_path = os.path.join(resources, "app.asar")
    cpm.AsarArchive.pack(src_dir, asar_path)
    orig_hash = cpm.sha256_file(asar_path)

    return app_path, asar_path, orig_hash


def _make_patch(patch_id="test", search=r"\w+\(`505458`\)", replace="!0",
                file_pattern="webview/assets/test-module-*.js"):
    """Create a minimal Patch for testing."""
    return cpm.Patch(
        id=patch_id,
        name="Test Patch",
        category="test",
        description="test",
        modifications=[
            cpm.Modification(file_pattern, search, replace, "test mod"),
        ],
    )


# ─── PatchState Tests ───────────────────────────────────────────────────────


class PatchStateTest(unittest.TestCase):
    """Verify PatchState load/save/is_stale behavior."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="state-test-")
        self._state_dir = Path(self.tmp) / "state"
        self._state_file = self._state_dir / "state.json"
        self._patchers = [
            patch.object(cpm, "STATE_DIR", self._state_dir),
            patch.object(cpm, "STATE_FILE", self._state_file),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_missing_file(self):
        """No state file → empty state."""
        state = cpm.PatchState.load()
        self.assertIsNone(state.installed_hash)
        self.assertEqual(state.applied_patches, [])

    def test_load_corrupt_json(self):
        """Non-JSON garbage → empty state (no crash)."""
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text("this is not json {{{")
        state = cpm.PatchState.load()
        self.assertIsNone(state.installed_hash)
        self.assertEqual(state.applied_patches, [])

    def test_load_valid_json_non_dict(self):
        """Valid JSON that isn't a dict (null, list) → empty state (no crash)."""
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text("null")
        state = cpm.PatchState.load()
        self.assertIsNone(state.installed_hash)
        self.assertEqual(state.applied_patches, [])

        self._state_file.write_text("[1, 2, 3]")
        state = cpm.PatchState.load()
        self.assertIsNone(state.installed_hash)
        self.assertEqual(state.applied_patches, [])

    def test_save_load_roundtrip(self):
        """Save with hash + patch IDs → load → same values."""
        state = cpm.PatchState(
            installed_hash="abc123",
            applied_patches=["patch-a", "patch-b"],
        )
        state.save()
        loaded = cpm.PatchState.load()
        self.assertEqual(loaded.installed_hash, "abc123")
        self.assertEqual(loaded.applied_patches, ["patch-a", "patch-b"])

    def test_is_stale_matching(self):
        """Same hash → not stale."""
        state = cpm.PatchState(installed_hash="abc123")
        self.assertFalse(state.is_stale("abc123"))

    def test_is_stale_mismatching(self):
        """Different hash → stale."""
        state = cpm.PatchState(installed_hash="abc123")
        self.assertTrue(state.is_stale("def456"))

    def test_is_stale_no_hash(self):
        """No stored hash → not stale (short-circuits)."""
        state = cpm.PatchState(installed_hash=None)
        self.assertFalse(state.is_stale("anything"))


# ─── commit_changes Tests ───────────────────────────────────────────────────


class CommitChangesTest(unittest.TestCase):
    """Verify commit_changes workflow including rollback."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="commit-test-")
        self._state_dir = Path(self.tmp) / "state"
        self._state_file = self._state_dir / "state.json"
        self._patchers = [
            patch.object(cpm, "STATE_DIR", self._state_dir),
            patch.object(cpm, "STATE_FILE", self._state_file),
        ]
        for p in self._patchers:
            p.start()
        self.app_path, self.asar_path, self.orig_hash = _make_test_app(self.tmp)

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_commit_success(self):
        """Successful commit saves hash and applied patch IDs."""
        bundle = StubBundle(self.app_path)
        patch = _make_patch()
        success, msg = cpm.commit_changes(bundle, [patch])
        self.assertTrue(success)
        state = cpm.PatchState.load()
        self.assertIsNotNone(state.installed_hash)
        self.assertNotEqual(state.installed_hash, self.orig_hash)
        self.assertEqual(state.applied_patches, ["test"])

    def test_commit_rollback_on_sig_failure(self):
        """Signature failure after install triggers rollback; state preserved."""
        # Set up prior state — rollback preserves existing applied_patches.
        prior_state = cpm.PatchState(
            installed_hash=self.orig_hash,
            applied_patches=["prior-patch"],
        )
        prior_state.save()

        bundle = StubBundle(self.app_path, verify_returns=[
            (False, "sig broken"),   # post-install check fails
            (True, "valid"),         # post-rollback check passes
        ])
        patch = _make_patch()
        success, msg = cpm.commit_changes(bundle, [patch])
        self.assertFalse(success)
        self.assertIn("rolled back", msg)
        # Asar restored to original.
        self.assertEqual(cpm.sha256_file(self.asar_path), self.orig_hash)
        # State: hash updated to original, prior applied_patches preserved.
        state = cpm.PatchState.load()
        self.assertEqual(state.installed_hash, self.orig_hash)
        self.assertIn("prior-patch", state.applied_patches)

    def test_commit_rollback_incomplete(self):
        """Both signature checks fail → rollback incomplete."""
        bundle = StubBundle(self.app_path, verify_returns=[
            (False, "sig broken"),   # post-install
            (False, "sig still broken"),  # post-rollback
        ])
        patch = _make_patch()
        success, msg = cpm.commit_changes(bundle, [patch])
        self.assertFalse(success)
        self.assertIn("rollback incomplete", msg)
        self.assertIn("signature invalid", msg)

    def test_commit_zero_match_warning(self):
        """Patch with no matching content → success with warning."""
        bundle = StubBundle(self.app_path)
        patch = _make_patch(search=r"\w+\(`999999`\)")
        success, msg = cpm.commit_changes(bundle, [patch])
        self.assertTrue(success)
        self.assertIn("0 matches", msg)

    def test_commit_empty_patch_list(self):
        """Empty patch list (revert-all) → success, empty applied_patches."""
        bundle = StubBundle(self.app_path)
        success, msg = cpm.commit_changes(bundle, [])
        self.assertTrue(success)
        state = cpm.PatchState.load()
        self.assertEqual(state.applied_patches, [])
        self.assertIsNotNone(state.installed_hash)


# ─── cmd_verify Tests ───────────────────────────────────────────────────────


class CmdVerifyTest(unittest.TestCase):
    """Verify cmd_verify closed-loop check."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="verify-test-")
        self._state_dir = Path(self.tmp) / "state"
        self._state_file = self._state_dir / "state.json"
        self._patchers = [
            patch.object(cpm, "STATE_DIR", self._state_dir),
            patch.object(cpm, "STATE_FILE", self._state_file),
        ]
        for p in self._patchers:
            p.start()
        self.app_path, self.asar_path, self.orig_hash = _make_test_app(self.tmp)

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_verify_missing_asar(self):
        """Missing app.asar → returns False."""
        os.remove(self.asar_path)
        bundle = StubBundle(self.app_path)
        result = cpm.cmd_verify([], bundle)
        self.assertFalse(result)

    def test_verify_clean_state(self):
        """Readable asar, valid sig, matching state → returns True."""
        bundle = StubBundle(self.app_path)
        # No patches and no state → clean.
        result = cpm.cmd_verify([], bundle)
        self.assertTrue(result)

    def test_verify_pending_reapply(self):
        """State records patches as applied but asar doesn't have them → True."""
        bundle = StubBundle(self.app_path)
        patch = _make_patch()
        # The asar has the gate call, so detect_state returns "not_applied".
        # State says it's applied → "pending reapply" warning, not failure.
        state = cpm.PatchState(
            installed_hash=self.orig_hash,
            applied_patches=["test"],
        )
        state.save()
        result = cpm.cmd_verify([patch], bundle)
        self.assertTrue(result)

    def test_verify_drift_detected(self):
        """Patches detected as applied but not in state → returns False."""
        # Create an asar where the patch is already applied (gate call absent).
        drift_tmp = os.path.join(self.tmp, "drift")
        os.makedirs(drift_tmp)
        app_path, asar_path, orig_hash = _make_test_app(
            drift_tmp,
            files={"webview/assets/test-module-abc123.js": "var x = !0;"},
        )
        bundle = StubBundle(app_path)
        patch = _make_patch()
        # detect_state returns "applied" (search pattern absent, file present).
        # State has empty applied_patches → drift detected.
        state = cpm.PatchState(
            installed_hash=orig_hash,
            applied_patches=[],
        )
        state.save()
        result = cpm.cmd_verify([patch], bundle)
        self.assertFalse(result)

    def test_verify_sig_invalid(self):
        """Invalid signature → returns False."""
        bundle = StubBundle(self.app_path, verify_returns=(False, "mock invalid"))
        result = cpm.cmd_verify([], bundle)
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
