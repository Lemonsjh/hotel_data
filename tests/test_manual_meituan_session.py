"""普通 Edge 手动入口及当前账号会话注入；不启动真实浏览器或访问网络。"""
import json
import sys
import tempfile
import unittest
from contextlib import nullcontext
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "OTA采集服务"), str(ROOT / "美团OTA数据采集代码")]
import panel
import platform_login as login
import meituan_page_capture as capture
import meituan_status_session as session
import meituan_promotion_performance_data as promotion


class ManualMeituanSessionTests(unittest.TestCase):
    def test_manual_entry_opens_normal_edge_without_playwright(self):
        with patch.object(login.os, "startfile", create=True) as start, patch.object(login, "sync_playwright") as automation:
            login.open_local_edge()
        start.assert_called_once_with("microsoft-edge:" + login.PLATFORMS["meituan"]["url"])
        automation.assert_not_called()

    def test_failed_open_has_an_actionable_error(self):
        with patch.object(login.os, "startfile", create=True, side_effect=OSError("missing")):
            with self.assertRaisesRegex(RuntimeError, "无法打开本机 Edge"):
                login.open_local_edge()

    def test_manual_route_does_not_launch_or_clear_helper(self):
        with patch.object(panel.runner, "load_settings", return_value={}), \
             patch.object(panel, "apply_form_to_settings", return_value={}), \
             patch.object(panel.runner, "save_json"), patch.object(login, "open_local_edge") as open_edge, \
             patch.object(login, "start") as start, patch.object(login, "cancel") as cancel:
            response = panel.app.test_client().post("/platform-login/meituan", data={"login_mode": "manual"})
        self.assertEqual(response.status_code, 302)
        open_edge.assert_called_once_with()
        start.assert_not_called()
        cancel.assert_not_called()

    def test_repeated_open_does_not_restart_an_active_helper(self):
        with patch.object(login, "read_status", return_value={"pid": 123}), \
             patch.object(login, "login_process_alive", return_value=True), \
             patch.object(login, "cancel") as cancel, patch.object(login.subprocess, "Popen") as spawn:
            self.assertEqual(login.start("meituan", {}), 123)
        cancel.assert_not_called()
        spawn.assert_not_called()

    def test_snapshot_matches_both_current_cookies(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory, patch.object(capture, "profile_path", return_value=Path(directory)):
            state = {"cookies": [{"name": "test", "value": "fake"}], "origins": []}
            context = MagicMock()
            context.storage_state.return_value = state
            capture.save_login_session(context, "me=new", "eb=new")
            self.assertEqual(capture.load_login_session("me=new", "eb=new"), state)
            self.assertIsNone(capture.load_login_session("me=other", "eb=new"))
            self.assertIsNone(capture.load_login_session("me=new", "eb=other"))
            self.assertIsNone(capture.load_login_session("", "eb=new"))
            self.assertFalse((Path(directory) / "collector_session.tmp").exists())

    def test_missing_or_invalid_snapshot_is_not_reused(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory, patch.object(capture, "profile_path", return_value=Path(directory)):
            self.assertIsNone(capture.load_login_session("me=new", "eb=new"))
            # Invalid state is simulated without reading or writing any real profile.
            for data in ("broken", "[]", json.dumps({"fingerprint": "different", "storage_state": {"cookies": []}})):
                with patch.object(Path, "read_text", return_value=data):
                    self.assertIsNone(capture.load_login_session("me=new", "eb=new"))

    def test_status_context_injects_only_the_current_configured_domains(self):
        playwright = MagicMock()
        with patch.multiple(session, MEITUAN_ME_COOKIE="me=current", MEITUAN_EB_COOKIE="eb=current",
                            MEITUAN_HIGHLIGHTS_COOKIE="tdc=current", MEITUAN_PUBLIC_WELFARE_COOKIE="gongyi=current"), \
             patch.object(session, "load_login_session", return_value=None):
            context = session.launch_status_context(playwright)
        chromium = playwright.chromium
        chromium.launch_persistent_context.assert_not_called()
        self.assertIsNone(chromium.launch.return_value.new_context.call_args.kwargs["storage_state"])
        self.assertEqual({(c["url"], c["name"], c["value"]) for c in context.add_cookies.call_args.args[0]}, {
            ("https://me.meituan.com/", "me", "current"), ("https://eb.meituan.com/", "eb", "current"),
            ("https://tdc.meituan.com/", "tdc", "current"), ("https://gongyi.meituan.com/", "gongyi", "current"),
        })

    def test_missing_main_cookie_fails_without_opening_a_browser(self):
        playwright = MagicMock()
        with patch.object(session, "MEITUAN_ME_COOKIE", ""):
            with self.assertRaisesRegex(RuntimeError, "ME、EB Cookie"):
                session.launch_status_context(playwright)
        playwright.chromium.launch.assert_not_called()

    def test_context_setup_failure_closes_browser(self):
        playwright = MagicMock()
        playwright.chromium.launch.return_value.new_context.side_effect = RuntimeError("invalid state")
        with patch.multiple(session, MEITUAN_ME_COOKIE="me=current", MEITUAN_EB_COOKIE="eb=current"), \
             patch.object(session, "load_login_session", return_value={}):
            with self.assertRaises(RuntimeError):
                session.launch_status_context(playwright)
        playwright.chromium.launch.return_value.close.assert_called_once_with()

    def test_promotion_cookie_injection_and_successful_empty_result(self):
        with patch.object(promotion, "sync_playwright") as factory, \
             patch.object(promotion, "browser_profile_lock", return_value=nullcontext()), \
             patch.multiple(promotion, MEITUAN_ME_COOKIE="me=current", MEITUAN_EB_COOKIE="eb=current", MEITUAN_PROMOTION_COOKIE="ad=current"), \
             patch.object(promotion, "request_page", return_value={"total": 0, "pageSize": 20, "planList": [], "pageNum": 1}) as request:
            self.assertEqual(promotion.fetch_plans(date(2026, 9, 21), date(2026, 10, 20)), [])
        context = factory.return_value.__enter__.return_value.chromium.launch_persistent_context.return_value
        cookies = context.add_cookies.call_args.args[0]
        ad = next(c for c in cookies if c["url"] == "https://ebmidas.dianping.com/")
        self.assertEqual((ad["name"], ad["value"], ad["sameSite"], ad["secure"]), ("ad", "current", "None", True))
        request.assert_called_once()
        context.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
