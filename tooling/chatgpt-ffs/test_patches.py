#!/usr/bin/env python3
"""Tests for patch definitions, labels, and patch state detection.

Run: python3 test_patches.py
"""

import os
import re
import sys
import tempfile
import unittest

import importlib.util
from importlib.machinery import SourceFileLoader

_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chatgpt-ffs")
_loader = SourceFileLoader("codex_patch_manager", _script)
_spec = importlib.util.spec_from_loader("codex_patch_manager", _loader)
cpm = importlib.util.module_from_spec(_spec)
_loader.exec_module(cpm)


class PatchDefinitionsTest(unittest.TestCase):
    """Verify built-in patch definitions are well-formed."""

    def setUp(self):
        self.patches = cpm.load_patches()

    def test_builtin_patches_loaded(self):
        """BUILTIN_PATCHES has at least 30 entries (29 feature + example external)."""
        self.assertGreaterEqual(len(self.patches), 30)

    def test_all_patches_have_required_fields(self):
        """Every patch has id, name, category, description, modifications, labels."""
        for p in self.patches:
            self.assertTrue(p.id, f"Patch missing id: {p}")
            self.assertTrue(p.name, f"Patch {p.id} missing name")
            self.assertTrue(p.category, f"Patch {p.id} missing category")
            self.assertTrue(p.description, f"Patch {p.id} missing description")
            self.assertIsInstance(p.labels, list, f"Patch {p.id} labels not a list")
            self.assertTrue(
                p.modifications or p.binary_modifications,
                f"Patch {p.id} has no modifications or binary_modifications"
            )

    def test_all_patch_ids_unique(self):
        """No two patches share the same id."""
        ids = [p.id for p in self.patches]
        duplicates = [pid for pid in ids if ids.count(pid) > 1]
        self.assertEqual(duplicates, [], f"Duplicate patch ids: {set(duplicates)}")

    def test_all_modifications_have_required_fields(self):
        """Every modification has file, search, replace, description."""
        for p in self.patches:
            for i, mod in enumerate(p.modifications):
                self.assertTrue(mod.file, f"Patch {p.id} mod {i} missing file")
                self.assertTrue(mod.search, f"Patch {p.id} mod {i} missing search")
                self.assertTrue(mod.replace, f"Patch {p.id} mod {i} missing replace")

    def test_all_search_patterns_are_valid_regex(self):
        """Every modification search pattern compiles as a regex."""
        for p in self.patches:
            for i, mod in enumerate(p.modifications):
                try:
                    re.compile(mod.search)
                except re.error as e:
                    self.fail(f"Patch {p.id} mod {i} invalid regex '{mod.search}': {e}")

    def test_all_labels_in_meta(self):
        """Every label used by a patch exists in LABEL_META."""
        for p in self.patches:
            for label in p.labels:
                self.assertIn(label, cpm.LABEL_META,
                              f"Patch {p.id} has unknown label '{label}'")

    def test_label_meta_covers_all_dimensions(self):
        """LABEL_META has entries for all four dimensions."""
        dims = set(d for d, _ in cpm.LABEL_META.values())
        self.assertEqual(dims, {"req", "prov", "aff", "beh"})

    def test_label_dimensions_count(self):
        """LABEL_DIMENSIONS has exactly 4 entries."""
        self.assertEqual(len(cpm.LABEL_DIMENSIONS), 4)

    def test_worktree_patch_has_labels(self):
        """The worktree-feature-flag patch has labels."""
        wt = next((p for p in self.patches if p.id == "worktree-feature-flag"), None)
        self.assertIsNotNone(wt, "worktree-feature-flag patch not found")
        self.assertTrue(wt.labels, "worktree patch has no labels")
        self.assertIn("local-only", wt.labels)
        self.assertIn("workspace", wt.labels)

    def test_avatar_overlay_has_four_gate_mods(self):
        """Avatar overlay patch has modifications for all 4 gate IDs."""
        ao = next((p for p in self.patches if p.id == "avatar-overlay"), None)
        self.assertIsNotNone(ao, "avatar-overlay patch not found")
        gate_ids = set()
        for mod in ao.modifications:
            # Extract gate ID from search pattern like \w+\(`1234567890`\)
            m = re.search(r"`(\d+)`", mod.search)
            if m:
                gate_ids.add(m.group(1))
        self.assertEqual(len(gate_ids), 4,
                         f"Expected 4 gate IDs, got {gate_ids}")
        for gid in ["1256703444", "1529702798", "1840974662", "4167858931"]:
            self.assertIn(gid, gate_ids, f"Gate {gid} not in avatar-overlay mods")


class PatchStateDetectionTest(unittest.TestCase):
    """Verify detect_state works on synthetic asar files."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="patch-test-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_synthetic_asar(self, files):
        """Create a minimal asar with the given {relpath: content} files."""
        src = os.path.join(self.tmp, "src")
        os.makedirs(src)
        for relpath, content in files.items():
            full = os.path.join(src, relpath)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as f:
                f.write(content if isinstance(content, bytes) else content.encode())
        asar = os.path.join(self.tmp, "test.asar")
        cpm.AsarArchive.pack(src, asar)
        return asar

    def test_detect_not_applied(self):
        """detect_state returns 'not_applied' when search pattern is present."""
        asar = self._make_synthetic_asar({
            "webview/assets/test-module-abc123.js": "var x = checkGate(`505458`);",
        })
        patch = cpm.Patch(
            id="test",
            name="Test",
            category="test",
            description="test",
            modifications=[
                cpm.Modification("webview/assets/test-module-*.js",
                                 r"\w+\(`505458`\)", "!0", "test"),
            ],
        )
        self.assertEqual(patch.detect_state(cpm.AsarArchive(asar)), "not_applied")

    def test_detect_applied(self):
        """detect_state returns 'applied' when search pattern is absent."""
        asar = self._make_synthetic_asar({
            "webview/assets/test-module-abc123.js": "var x = !0;",
        })
        patch = cpm.Patch(
            id="test",
            name="Test",
            category="test",
            description="test",
            modifications=[
                cpm.Modification("webview/assets/test-module-*.js",
                                 r"\w+\(`505458`\)", "!0", "test"),
            ],
        )
        self.assertEqual(patch.detect_state(cpm.AsarArchive(asar)), "applied")

    def test_detect_unknown_when_file_missing(self):
        """detect_state returns 'unknown' when no matching files exist."""
        asar = self._make_synthetic_asar({
            "other.js": "var x = 1;",
        })
        patch = cpm.Patch(
            id="test",
            name="Test",
            category="test",
            description="test",
            modifications=[
                cpm.Modification("webview/assets/missing-*.js",
                                 r"\w+\(`505458`\)", "!0", "test"),
            ],
        )
        self.assertEqual(patch.detect_state(cpm.AsarArchive(asar)), "unknown")

    def test_apply_to_replaces_gate_call(self):
        """apply_to replaces checkGate calls with !0."""
        src = os.path.join(self.tmp, "extracted")
        os.makedirs(os.path.join(src, "webview", "assets"))
        test_file = os.path.join(src, "webview", "assets", "test-module-abc123.js")
        with open(test_file, "w") as f:
            f.write("var a = Ql(`505458`); var b = pe(`505458`);")

        mod = cpm.Modification("webview/assets/test-module-*.js",
                               r"\w+\(`505458`\)", "!0", "test")
        count = mod.apply_to(src)
        self.assertEqual(count, 2)

        with open(test_file, "r") as f:
            result = f.read()
        self.assertEqual(result, "var a = !0; var b = !0;")


    def test_apply_to_handles_method_call(self):
        """apply_to replaces n?.checkGate(`GATE`) without breaking syntax.

        The regex must capture the full method-call chain (n?.checkGate)
        so the replacement produces valid JS (!0), not n?.!0.
        """
        src = os.path.join(self.tmp, "extracted")
        os.makedirs(os.path.join(src, "webview", "assets"))
        test_file = os.path.join(src, "webview", "assets", "test-module-abc123.js")
        with open(test_file, "w") as f:
            f.write("t||n?.checkGate(`505458`)===!0")

        mod = cpm.Modification("webview/assets/test-module-*.js",
                               r"[\w$?.]+\(`505458`\)", "!0", "test")
        count = mod.apply_to(src)
        self.assertEqual(count, 1)

        with open(test_file, "r") as f:
            result = f.read()
        self.assertEqual(result, "t||!0===!0")
        self.assertNotIn("n?.!0", result)

    def test_detect_state_with_non_call_gate_id(self):
        """detect_state returns 'applied' when gate ID survives in a
        non-call context (e.g. variable assignment) after patching."""
        asar = self._make_synthetic_asar({
            "webview/assets/test-module-abc123.js":
                "Ezt=`505458`,SD=G(Y,({get:e})=>{return !0===!0})",
        })
        patch = cpm.Patch(
            id="test",
            name="Test",
            category="test",
            description="test",
            modifications=[
                cpm.Modification("webview/assets/test-module-*.js",
                                 r"[\w$?.]+\(`505458`\)", "!0", "test"),
            ],
        )
        self.assertEqual(patch.detect_state(cpm.AsarArchive(asar)), "applied")


class ExternalPatchLabelsTest(unittest.TestCase):
    """Verify external JSON patches support labels."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ext-patch-test-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_external_patch_with_labels(self):
        """External JSON patch with labels field loads correctly."""
        import json
        patch_data = [{
            "id": "ext-test",
            "name": "External Test",
            "category": "test",
            "labels": ["local-only", "user-facing"],
            "description": "External patch for testing",
            "modifications": [{
                "file": "test-*.js",
                "search": r"\w+\(`999999`\)",
                "replace": "!0",
                "description": "test mod"
            }]
        }]

        ext_dir = os.path.join(self.tmp, "patches.d")
        os.makedirs(ext_dir)
        with open(os.path.join(ext_dir, "test.json"), "w") as f:
            json.dump(patch_data, f)

        # Temporarily override EXTERNAL_PATCHES_DIR
        patches = cpm.load_patches(external_dir=cpm.Path(ext_dir))

        ext = next((p for p in patches if p.id == "ext-test"), None)
        self.assertIsNotNone(ext, "External patch not loaded")
        self.assertEqual(ext.labels, ["local-only", "user-facing"])

    def test_external_patch_without_labels_defaults_empty(self):
        """External JSON patch without labels field defaults to empty list."""
        import json
        patch_data = [{
            "id": "ext-no-labels",
            "name": "External No Labels",
            "category": "test",
            "description": "External patch without labels",
            "modifications": [{
                "file": "test-*.js",
                "search": r"\w+\(`888888`\)",
                "replace": "!0",
                "description": "test mod"
            }]
        }]

        ext_dir = os.path.join(self.tmp, "patches.d")
        os.makedirs(ext_dir)
        with open(os.path.join(ext_dir, "nolabels.json"), "w") as f:
            json.dump(patch_data, f)

        patches = cpm.load_patches(external_dir=cpm.Path(ext_dir))

        ext = next((p for p in patches if p.id == "ext-no-labels"), None)
        self.assertIsNotNone(ext, "External patch not loaded")
        self.assertEqual(ext.labels, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
