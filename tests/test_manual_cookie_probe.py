import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "OTA采集服务"))
"""手动填写的 Cookie 必须用于识别，不能回退到旧浏览器会话。"""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import hotel_name_probe as probe


class ManualCookieProbeTests(unittest.TestCase):
    def test_supplied_cookies_override_existing_profile(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory, patch.dict(os.environ, {"LOCALAPPDATA": directory}), patch.object(probe, "sync_playwright") as factory:
            (Path(directory) / "HotelAgent/browser_profiles/meituan").mkdir(parents=True)
            playwright = factory.return_value.__enter__.return_value
            browser = playwright.chromium.launch.return_value
            context = browser.new_context.return_value
            page = context.new_page.return_value
            page.url = probe.MEITUAN_URL
            page.locator.return_value.inner_text.return_value = "测试酒店"
            page.evaluate.return_value = []
            page.title.return_value = "酒店后台"
            handlers = {}
            page.on.side_effect = lambda name, callback: handlers.update({name: callback})
            signed_url = "https://me.meituan.com" + probe.MEITUAN_COMMENT_PATH + "?poiId=1&partnerId=2&bizAccountId=3&mtgsig=test"

            def loaded(*args, **kwargs):
                handlers["request"](SimpleNamespace(url=signed_url))
                handlers["response"](SimpleNamespace(url=signed_url, json=lambda: {"data": {"poiName": "测试酒店"}}))

            page.goto.side_effect = loaded
            result = probe.probe("meituan", "mebsid=manual_me", "ebbsid=manual_eb")
            self.assertTrue(result["ok"])
            self.assertEqual(result["hotel_name"], "测试酒店")
            self.assertEqual(result["session_source"], "cookie")
            self.assertEqual((result["poi_id"], result["partner_id"], result["biz_account_id"]), ("1", "2", "3"))
            cookies = context.add_cookies.call_args.args[0]
            self.assertTrue(any(c["name"] == "mebsid" and c["value"] == "manual_me" and c["domain"] == "me.meituan.com" for c in cookies))
            self.assertTrue(any(c["name"] == "ebbsid" and c["value"] == "manual_eb" and c["domain"] == "eb.meituan.com" for c in cookies))
            playwright.chromium.launch_persistent_context.assert_not_called()
            context.close.assert_called_once_with()
            browser.close.assert_called_once_with()

    def test_invalid_cookie_login_redirect_does_not_fall_back_to_profile(self):
        with patch.object(probe, "sync_playwright") as factory:
            playwright = factory.return_value.__enter__.return_value
            page = playwright.chromium.launch.return_value.new_context.return_value.new_page.return_value
            page.url = "https://me.meituan.com/login/index.html"
            page.locator.return_value.inner_text.return_value = "请登录"
            page.evaluate.return_value = []
            page.title.return_value = "登录"
            result = probe.probe("meituan", "mebsid=invalid")
            self.assertFalse(result["ok"])
            self.assertTrue(result["login_like"])
            self.assertEqual(result["hotel_name"], "")
            playwright.chromium.launch_persistent_context.assert_not_called()

    def test_scoped_cookie_probe_does_not_expand_domain(self):
        with patch.object(probe, "sync_playwright") as factory:
            context = factory.return_value.__enter__.return_value.chromium.launch.return_value.new_context.return_value
            page = context.new_page.return_value
            page.url = "https://me.meituan.com/login/index.html"
            page.locator.return_value.inner_text.return_value = "请登录"
            page.evaluate.return_value = []
            page.title.return_value = "登录"
            scoped = [{"name": "session", "value": "fake", "domain": "me.meituan.com", "path": "/"}]
            probe.probe("meituan", "session=fake", scoped_cookies=scoped)
            context.add_cookies.assert_called_once_with(scoped)

    def test_empty_cookie_does_not_open_browser(self):
        with patch.object(probe, "sync_playwright") as factory:
            self.assertFalse(probe.probe("meituan", "")["ok"])
            factory.assert_not_called()

    def test_old_blank_form_preserves_detected_identity_when_cookie_is_updated(self):
        import panel
        identity = {"hotel_name": "测试酒店", "poi_id": "1", "partner_id": "2", "biz_account_id": "3"}
        settings = {"meituan": {**identity, "eb_cookie": "old"}}
        form = {"meituan." + key: "" for key in identity}
        form["meituan.eb_cookie"] = "ebbsid=new"
        with panel.app.test_request_context("/config", method="POST", data=form):
            result = panel.apply_form_to_settings(settings)
        self.assertEqual({key: result["meituan"][key] for key in identity}, identity)
        self.assertEqual(result["meituan"]["eb_cookie"], "ebbsid=new")
        with panel.app.test_request_context("/config", method="POST", data={"meituan.poi_id": "4"}):
            self.assertEqual(panel.apply_form_to_settings(settings)["meituan"]["poi_id"], "4")


if __name__ == "__main__":
    unittest.main()
