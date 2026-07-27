import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "libexec/hindsight-embed-ui-compat.py"


def load_helper():
    spec = importlib.util.spec_from_file_location(
        "hindsight_embed_ui_compat", HELPER
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UiCompatibilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = load_helper()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.package = Path(self.temporary.name) / "control-plane"
        next_root = self.package / "standalone" / ".next"
        self.chunk = next_root / "server" / "edge" / "chunks" / "middleware.js"
        self.chunk.parent.mkdir(parents=True)
        self.chunk.write_text(
            "prefix hindsight_cp_access "
            + self.helper.AUTH_NEEDLE
            + " middle "
            + self.helper.ROUTING_NEEDLE
            + " suffix",
            encoding="utf-8",
        )
        manifest = next_root / "server" / "middleware-manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(
                {
                    "middleware": {
                        "/": {
                            "files": [
                                "server/edge/chunks/middleware.js",
                            ]
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

    def test_patch_is_exact_and_idempotent(self):
        self.assertTrue(self.helper.patch_package(self.package))
        content = self.chunk.read_text(encoding="utf-8")
        self.assertIn(self.helper.AUTH_REPLACEMENT, content)
        self.assertIn(self.helper.PATCH_MARKER, content)
        self.assertNotIn(self.helper.AUTH_NEEDLE, content)
        self.assertNotIn(self.helper.ROUTING_NEEDLE, content)
        self.assertFalse(self.helper.patch_package(self.package))

    def test_patch_fails_closed_on_unknown_middleware(self):
        self.chunk.write_text(
            "prefix hindsight_cp_access unknown contract",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            self.helper.CompatibilityError, "approved contract"
        ):
            self.helper.patch_package(self.package)


if __name__ == "__main__":
    unittest.main()
