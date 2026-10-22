import shutil
import subprocess
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ExtensionSourceTests(unittest.TestCase):
    def test_extension_loads_directly_from_project_without_build_step(self):
        source = ROOT / "extensions/meituan-login-sync"
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        for relative in (manifest["background"]["service_worker"], manifest["side_panel"]["default_path"],
                         "cookies.js", "browser_verify.js", "popup.css", "popup.js", "README.md"):
            self.assertTrue((source / relative).is_file(), relative)
        self.assertFalse((source / "settings.json").exists())

    @unittest.skipUnless(shutil.which("node"), "Node is only needed for extension developer tests")
    def test_extension_javascript_with_mock_browser(self):
        result = subprocess.run([shutil.which("node"), "--experimental-vm-modules",
                                 str(ROOT / "tests/test_browser_cookie_extension.mjs")],
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
