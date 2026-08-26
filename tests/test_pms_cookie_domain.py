from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "正式数据抓取-PMS（别样红）" / "PMS登录" / "scripts"


def load_pms_utils():
    sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location("pms_utils_cookie_domain_test_target", SCRIPTS_DIR / "pms_utils.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load pms_utils.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Context:
    def __init__(self):
        self.cookies = []

    def add_cookies(self, cookies):
        self.cookies.extend(cookies)


class PmsCookieDomainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_pms_utils()

    def test_cookie_domain_uses_current_pms_report_host(self):
        context = _Context()
        with patch.object(self.module, "REPORT_BASE_URL", "https://yinmo.beyondh.com:8081"):
            self.module.add_cookies_to_context(context, {"SessionId": "test"})
        self.assertEqual(context.cookies[0]["domain"], "yinmo.beyondh.com")


if __name__ == "__main__":
    unittest.main()
