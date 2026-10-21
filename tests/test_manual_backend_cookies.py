import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "OTA采集服务"))
import os
import unittest
from unittest.mock import patch

import panel
import runner


class ManualBackendCookieTests(unittest.TestCase):
    def test_backend_cookie_save_and_blank_preservation(self):
        settings = {"meituan": {"me_cookie": "mebsid=keep", "eb_cookie": "ebbsid=keep", "promotion_cookie": "adpbsid=keep"}}
        values = {"meituan.highlights_cookie": "tdc=manual", "meituan.public_welfare_cookie": "gongyi=manual"}
        with panel.app.test_request_context("/config", method="POST", data=values):
            result = panel.apply_form_to_settings(settings)
        self.assertEqual(result["meituan"]["highlights_cookie"], "tdc=manual")
        self.assertEqual(result["meituan"]["public_welfare_cookie"], "gongyi=manual")
        with panel.app.test_request_context("/config", method="POST", data={key: "" for key in values}):
            preserved = panel.apply_form_to_settings(result)
        self.assertEqual(preserved["meituan"], result["meituan"])
        self.assertEqual(preserved["meituan"]["eb_cookie"], "ebbsid=keep")
        self.assertEqual(preserved["meituan"]["promotion_cookie"], "adpbsid=keep")

    def test_config_overrides_inherited_cookies_even_when_empty(self):
        with patch.dict(os.environ, {"MEITUAN_HIGHLIGHTS_COOKIE": "stale", "MEITUAN_PUBLIC_WELFARE_COOKIE": "stale"}):
            empty = runner.build_env({"meituan": {}}, "meituan")
            current = runner.build_env({"meituan": {"highlights_cookie": "tdc=current", "public_welfare_cookie": "gongyi=current"}}, "meituan")
        self.assertEqual(empty["MEITUAN_HIGHLIGHTS_COOKIE"], "")
        self.assertEqual(empty["MEITUAN_PUBLIC_WELFARE_COOKIE"], "")
        self.assertEqual(current["MEITUAN_HIGHLIGHTS_COOKIE"], "tdc=current")
        self.assertEqual(current["MEITUAN_PUBLIC_WELFARE_COOKIE"], "gongyi=current")

    def test_backend_cookie_values_are_redacted(self):
        settings = {"meituan": {"highlights_cookie": "tdc=private-session", "public_welfare_cookie": "gongyi=private-session"}}
        text = runner.sanitize("tdc=private-session; gongyi=private-session", settings)
        self.assertNotIn("private-session", text)


if __name__ == "__main__":
    unittest.main()
