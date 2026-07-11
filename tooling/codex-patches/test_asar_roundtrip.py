#!/usr/bin/env python3
"""Tests for asar extract/pack round-trip with unpacked entries.

Run: python3 test_asar_roundtrip.py
"""

import os
import shutil
import sys
import tempfile
import unittest

# Import from the script in the same directory.  The filename contains
# hyphens and lacks a .py extension, so we load it via importlib with an
# explicit SourceFileLoader.
import importlib.util
from importlib.machinery import SourceFileLoader
_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "codex-patch-manager")
_loader = SourceFileLoader("codex_patch_manager", _script)
_spec = importlib.util.spec_from_loader("codex_patch_manager", _loader)
cpm = importlib.util.module_from_spec(_spec)
_loader.exec_module(cpm)


class AsarRoundTripTest(unittest.TestCase):
    """Round-trip a synthetic asar through extract → pack and verify the
    repacked archive preserves both packed and unpacked entries."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="asar-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── helpers ──────────────────────────────────────────────────────────

    def _make_src(self, files):
        """Create a source directory with the given {relpath: content} files."""
        src = os.path.join(self.tmp, "src")
        os.makedirs(src)
        for relpath, content in files.items():
            full = os.path.join(src, relpath)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as f:
                f.write(content)
        return src

    def _make_unpacked_dir(self, asar_path, files):
        """Create the sibling .unpacked/ directory with unpacked file contents."""
        unpacked_dir = asar_path + ".unpacked"
        os.makedirs(unpacked_dir, exist_ok=True)
        for relpath, content in files.items():
            full = os.path.join(unpacked_dir, relpath)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as f:
                f.write(content)
        return unpacked_dir

    # ── tests ────────────────────────────────────────────────────────────

    def test_roundtrip_preserves_unpacked_entry(self):
        """A packed file and an unpacked file survive extract → pack."""
        packed_data = b"hello packed world"
        unpacked_data = b"\x00\x01\x02\x03native-module-bytes"

        src = self._make_src({
            "packed.txt": packed_data,
            "unpacked.node": unpacked_data,
        })

        # Pack with one file marked unpacked.
        asar1 = os.path.join(self.tmp, "test1.asar")
        cpm.pack_asar(src, asar1, unpacked_paths={"unpacked.node"})

        # pack_asar excludes unpacked files from the payload, so we must
        # provide the sibling .unpacked/ directory for extract to find them.
        self._make_unpacked_dir(asar1, {"unpacked.node": unpacked_data})

        # Extract — should return the set of unpacked paths.
        extracted = os.path.join(self.tmp, "extracted")
        result = cpm.extract_asar(asar1, extracted)

        self.assertEqual(result, {"unpacked.node"})
        self.assertTrue(os.path.isfile(os.path.join(extracted, "packed.txt")))
        self.assertTrue(os.path.isfile(os.path.join(extracted, "unpacked.node")))

        # Repack.
        asar2 = os.path.join(self.tmp, "test2.asar")
        cpm.pack_asar(extracted, asar2, unpacked_paths=result)

        # Verify the repacked header.
        header, fds = cpm.read_asar_header(asar2)
        files = header["files"]

        # Packed file: has offset, no unpacked flag.
        self.assertIn("packed.txt", files)
        self.assertIn("offset", files["packed.txt"])
        self.assertNotIn("unpacked", files["packed.txt"])

        # Unpacked file: has unpacked=True, size, integrity, NO offset.
        self.assertIn("unpacked.node", files)
        self.assertTrue(files["unpacked.node"].get("unpacked"))
        self.assertNotIn("offset", files["unpacked.node"])
        self.assertEqual(files["unpacked.node"]["size"], len(unpacked_data))
        self.assertIn("integrity", files["unpacked.node"])

        # Packed file data is readable from the asar binary.
        data = cpm.read_asar_file(asar2, "packed.txt")
        self.assertEqual(data, packed_data)

        # Unpacked file data is NOT in the asar binary (no offset).
        data = cpm.read_asar_file(asar2, "unpacked.node")
        self.assertIsNone(data)

    def test_roundtrip_nested_unpacked(self):
        """An unpacked file nested in a subdirectory survives round-trip."""
        packed_data = b"root file"
        unpacked_data = b"nested native"

        src = self._make_src({
            "root.txt": packed_data,
            "node_modules/better-sqlite3/build/Release/better_sqlite3.node": unpacked_data,
        })

        unpacked_rel = "node_modules/better-sqlite3/build/Release/better_sqlite3.node"

        asar1 = os.path.join(self.tmp, "nested1.asar")
        cpm.pack_asar(src, asar1, unpacked_paths={unpacked_rel})

        self._make_unpacked_dir(asar1, {unpacked_rel: unpacked_data})

        extracted = os.path.join(self.tmp, "extracted")
        result = cpm.extract_asar(asar1, extracted)

        self.assertEqual(result, {unpacked_rel})
        self.assertTrue(os.path.isfile(os.path.join(extracted, "root.txt")))
        self.assertTrue(os.path.isfile(os.path.join(extracted, unpacked_rel)))

        asar2 = os.path.join(self.tmp, "nested2.asar")
        cpm.pack_asar(extracted, asar2, unpacked_paths=result)

        header, fds = cpm.read_asar_header(asar2)
        node = header["files"]["node_modules"]["files"]["better-sqlite3"]["files"]["build"]["files"]["Release"]["files"]["better_sqlite3.node"]

        self.assertTrue(node.get("unpacked"))
        self.assertNotIn("offset", node)
        self.assertEqual(node["size"], len(unpacked_data))

        root_node = header["files"]["root.txt"]
        self.assertIn("offset", root_node)
        self.assertNotIn("unpacked", root_node)

    def test_pack_without_unpacked_paths_works(self):
        """Backward compat: pack_asar without unpacked_paths packs everything normally."""
        src = self._make_src({"file.txt": b"content"})
        asar = os.path.join(self.tmp, "compat.asar")
        cpm.pack_asar(src, asar)

        header, fds = cpm.read_asar_header(asar)
        node = header["files"]["file.txt"]
        self.assertIn("offset", node)
        self.assertNotIn("unpacked", node)
        self.assertEqual(cpm.read_asar_file(asar, "file.txt"), b"content")

    def test_extract_without_unpacked_dir(self):
        """Extract gracefully handles missing .unpacked/ directory."""
        src = self._make_src({"packed.txt": b"data"})
        asar = os.path.join(self.tmp, "no-unpacked.asar")
        cpm.pack_asar(src, asar)

        extracted = os.path.join(self.tmp, "extracted")
        result = cpm.extract_asar(asar, extracted)

        self.assertEqual(result, set())
        self.assertTrue(os.path.isfile(os.path.join(extracted, "packed.txt")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
