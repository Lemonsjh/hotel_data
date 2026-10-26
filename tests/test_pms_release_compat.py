from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "updater"), str(ROOT / "OTA采集服务"),
                str(ROOT / "别样红PMS数据采集代码/PMS登录/scripts")]
import build_release
import package_format as fmt
import local_reset
import pms_providers
import pms_utils
import price_tasks
import runner

CURRENT = "别样红PMS数据采集代码"
LEGACY = "正式数据抓取-PMS（别样红）"


class PmsReleaseCompatibilityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def put(self, name, content=b"# fixture\n"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def tracked_fixture(self):
        files = ["mysql_connection.py", "ota_mysql_writer.py", "OTA采集服务/runner.py",
                 "OTA采集服务/panel.py", f"{CURRENT}/PMS登录/fetch_main.py",
                 f"{CURRENT}/ota调价/meituan_change_price.py"]
        for name in files:
            self.put(name)
        self.put("requirements-portable.lock.txt", b"example==1\n")
        self.put(f"{CURRENT}/PMS登录/pms_session_playwright.json", b'{"Token":"DO_NOT_SHIP"}')
        self.put("OTA采集服务/config/settings.json", b'{"password":"DO_NOT_SHIP"}')
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)

    def test_default_asset_passes_old_directory_allowlist_without_rewriting_source(self):
        self.tracked_fixture()
        source = self.root / CURRENT / "PMS登录/fetch_main.py"
        before = source.read_bytes()
        package = build_release.build(self.root, self.root / "dist", "v0.0.4", [], include_browser_extension=False)
        with patch.object(fmt, "CODE_DIRS", fmt.CODE_DIRS - {CURRENT}):
            manifest, content = fmt.validate_package(package, self.root, "v0.0.4")
        self.assertIn(f"{LEGACY}/PMS登录/fetch_main.py", content)
        self.assertFalse(any(name.startswith(CURRENT + "/") for name in content))
        self.assertEqual(content[f"{LEGACY}/PMS登录/fetch_main.py"], before)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(manifest["removed"], [])
        self.assertNotIn(b"DO_NOT_SHIP", b"".join(content.values()))

    def test_modern_layout_is_explicit_and_still_rejected_by_old_updater(self):
        self.tracked_fixture()
        package = build_release.build(self.root, self.root / "dist", "v0.0.4", [],
                                      include_browser_extension=False, legacy_pms_layout=False)
        with zipfile.ZipFile(package) as archive:
            self.assertIn(f"{CURRENT}/PMS登录/fetch_main.py", archive.namelist())
        with patch.object(fmt, "CODE_DIRS", fmt.CODE_DIRS - {CURRENT}), self.assertRaises(ValueError):
            fmt.validate_package(package, self.root)

    def test_installed_manifest_selects_updated_tree_even_when_both_exist(self):
        for name in (CURRENT, LEGACY):
            self.put(f"{name}/PMS登录/fetch_main.py")
        with patch.object(pms_providers, "PROJECT_ROOT", self.root):
            for managed in (LEGACY, CURRENT):
                self.put("updater/installed.json", json.dumps({
                    "files": {f"{managed}/PMS登录/fetch_main.py": "checksum"}}).encode())
                for configured in (None, f"{LEGACY}/PMS登录", f"{CURRENT}/PMS登录"):
                    self.assertEqual(pms_providers.byh_path(configured), self.root / managed / "PMS登录")
                self.assertEqual(price_tasks.price_dir(), self.root / managed / "ota调价")

    def test_new_default_and_external_custom_paths_work_in_legacy_only_deployment(self):
        self.put(f"{LEGACY}/PMS登录/fetch_main.py")
        with patch.object(pms_providers, "PROJECT_ROOT", self.root):
            self.assertEqual(pms_providers.byh_path(), self.root / LEGACY / "PMS登录")
            custom = ROOT.parent / "custom-pms" / "PMS登录"
            self.assertEqual(pms_providers.byh_path(custom), custom)

    def test_new_directory_reuses_legacy_session_without_copying_or_changing_it(self):
        source = self.put(f"{CURRENT}/PMS登录/scripts/pms_utils.py")
        old = self.put(f"{LEGACY}/PMS登录/pms_session_playwright.json", b'{"cookies":"fake"}')
        with patch.object(pms_utils, "__file__", str(source)):
            self.assertEqual(pms_utils.session_path(), old)
            current = self.put(f"{CURRENT}/PMS登录/pms_session_playwright.json", b'{"cookies":"new"}')
            self.assertEqual(pms_utils.session_path(), current)
        self.assertEqual(old.read_bytes(), b'{"cookies":"fake"}')
        self.assertEqual(current.read_bytes(), b'{"cookies":"new"}')

    def test_explicit_reset_cleans_both_session_paths_without_touching_other_files(self):
        current = self.put(f"{CURRENT}/PMS登录/pms_session_playwright.json")
        old = self.put(f"{LEGACY}/PMS登录/pms_session_playwright.json")
        data = self.put(f"{LEGACY}/PMS登录/output/local.json", b"keep")
        with patch.object(runner, "PROJECT_ROOT", self.root), \
             patch.object(local_reset, "PMS_SESSION_PATH", current), \
             patch.object(local_reset.platform_login, "PLATFORMS", {}):
            local_reset.clear_login_sessions()
        self.assertFalse(current.exists())
        self.assertFalse(old.exists())
        self.assertEqual(data.read_bytes(), b"keep")

    def test_legacy_compatibility_code_can_reuse_existing_modern_session(self):
        source = self.put(f"{LEGACY}/PMS登录/scripts/pms_utils.py")
        session = self.put(f"{CURRENT}/PMS登录/pms_session_playwright.json", b'{"cookies":"keep"}')
        with patch.object(pms_utils, "__file__", str(source)):
            self.assertEqual(pms_utils.session_path(), session)
        self.assertEqual(session.read_bytes(), b'{"cookies":"keep"}')


if __name__ == "__main__":
    unittest.main()
