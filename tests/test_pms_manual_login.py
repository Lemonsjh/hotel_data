from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "OTA采集服务"), str(ROOT / "别样红PMS数据采集代码/PMS登录/scripts")]
import panel
import platform_login as helper
import pms_utils as auth
import pms_manual_login as manual
import pms_utils as utils
import runner
from settings_validation import validate_settings


class ManualLoginTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir=ROOT)))
        self.path = self.folder / "pms_session.json"
        self.path.write_text('{"old": true}', encoding="utf-8")
        self.settings = {"pms": {"provider": "byh", "enabled": True, "login_mode": "manual",
                                 "hotel_name": "测试酒店", "username": "", "password": ""},
                         "hotel": {"hotel_id": "TEST"}, "mysql": {"database": "test"}}
        self.page = Mock(url="https://example.test/home")
        self.context = Mock(pages=[self.page])
        self.cookies = dict.fromkeys(utils.REQUIRED_COOKIES, "fake")
        self.context.cookies.return_value = [{"name": k, "value": v} for k, v in self.cookies.items()]
        playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=Mock(return_value=self.context)))
        self.launch = playwright.chromium.launch_persistent_context
        for target, name, value in (
            (runner, "CONFIG_PATH", self.folder / "config/settings.json"),
            (utils, "SESSION_PATH", self.path),
        ):
            self.stack.enter_context(patch.object(target, name, value))
        self.stack.enter_context(patch.object(runner, "load_settings", return_value=self.settings))
        self.stack.enter_context(patch.object(manual, "ensure_idle"))
        self.stack.enter_context(patch.object(manual, "load_pms_utils", return_value=utils))
        self.stack.enter_context(patch("playwright.sync_api.sync_playwright", return_value=nullcontext(playwright)))
        self.stack.enter_context(patch.object(helper, "stop_path", return_value=self.folder / "stop"))
        self.status = self.stack.enter_context(patch.object(helper, "write_status"))
        self.stack.enter_context(patch.object(helper, "show_success"))
        self.keep_open = self.stack.enter_context(patch.object(helper, "keep_open"))
        self.check = self.stack.enter_context(patch.object(auth, "check_identity", return_value="测试酒店"))

    def assert_old(self):
        self.assertEqual(json.loads(self.path.read_text()), {"old": True})

    def test_visible_login_saves_standard_session_only_after_verification(self):
        self.assertEqual(manual.run(), 0)
        self.assertFalse(self.launch.call_args.kwargs["headless"])
        self.assertEqual(self.launch.call_args.kwargs["channel"], "msedge")
        self.page.goto.assert_called_once_with(auth.pms_config.LOGIN_BASE_URL.removesuffix("/login") + "/", wait_until="domcontentloaded",
                                              timeout=auth.pms_config.NAVIGATION_TIMEOUT_MS)
        self.page.fill.assert_not_called()
        self.context.clear_cookies.assert_not_called()
        self.keep_open.assert_called_once_with(self.context, "pms")
        self.check.assert_called_once_with(self.cookies)
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["cookies"], self.cookies)
        self.assertEqual(saved["hotel_name"], "测试酒店")
        self.assertEqual(saved["hotel_id"], "TEST")
        self.assertEqual(saved["login_mode"], "manual")
        self.context.close.assert_called_once()

    def test_fresh_profile_opens_login_without_clearing_cookies(self):
        cookies = self.context.cookies.return_value
        self.context.cookies.side_effect = [[], cookies]
        self.assertEqual(manual.run(), 0)
        self.page.goto.assert_called_once_with(auth.pms_config.LOGIN_URL, wait_until="domcontentloaded",
                                              timeout=auth.pms_config.NAVIGATION_TIMEOUT_MS)
        self.context.clear_cookies.assert_not_called()
        self.keep_open.assert_not_called()

    def test_browser_session_cookie_loss_restores_saved_session(self):
        saved = {"url": auth.pms_config.LOGIN_BASE_URL, "cookies": self.cookies, "hotel_id": "TEST"}
        self.path.write_text(json.dumps(saved), encoding="utf-8")
        cookies = self.context.cookies.return_value
        self.context.cookies.side_effect = [[], cookies, cookies]
        with patch.dict("os.environ", {"HOTEL_ID": "TEST"}):
            self.assertEqual(manual.run(), 0)
        restored = {item["name"]: item["value"] for item in self.context.add_cookies.call_args.args[0]}
        self.assertEqual(restored, self.cookies)
        self.check.assert_called_once_with(self.cookies)
        self.context.clear_cookies.assert_not_called()
        self.keep_open.assert_called_once()

    def test_changed_server_does_not_restore_old_cookie(self):
        saved = {"url": "https://other.test:8101", "cookies": self.cookies}
        self.path.write_text(json.dumps(saved), encoding="utf-8")
        self.context.cookies.side_effect = [[], self.context.cookies.return_value]
        self.assertEqual(manual.run(), 0)
        self.context.add_cookies.assert_not_called()
        self.assertEqual(self.page.goto.call_args.args[0], auth.pms_config.LOGIN_URL)

    def test_new_hotel_overwrites_cookie_and_pms_name_without_switching_database(self):
        before = copy.deepcopy(self.settings)
        self.check.return_value = "其他酒店"
        self.assertEqual(manual.run(), 0)
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        config = json.loads(runner.CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(saved["hotel_name"], "其他酒店")
        self.assertEqual(saved["cookies"], self.cookies)
        self.assertEqual(config["pms"]["hotel_name"], "其他酒店")
        self.assertEqual(config["mysql"], before["mysql"])
        self.assertEqual(config["hotel"], before["hotel"])
        self.assertEqual(config["pms"]["username"], before["pms"]["username"])
        self.assertEqual(config["pms"]["password"], before["pms"]["password"])

    def test_network_failure_cancel_and_config_race_preserve_old(self):
        self.check.side_effect = auth.SessionCheckError("网络核验失败")
        with self.assertRaises(auth.SessionCheckError):
            manual.run()
        self.assert_old()
        self.check.side_effect = None
        self.check.return_value = "测试酒店"
        (self.folder / "stop").touch()
        with self.assertRaises(helper.LoginCancelled):
            manual.run()
        self.assert_old()
        (self.folder / "stop").unlink()
        changed = copy.deepcopy(self.settings)
        changed["mysql"]["database"] = "other"
        with patch.object(runner, "load_settings", side_effect=[self.settings, changed]):
            with self.assertRaisesRegex(RuntimeError, "配置已变化"):
                manual.run()
        self.assert_old()

    def test_close_browser_before_auth_preserves_old_session(self):
        self.context.pages = []
        with self.assertRaises(helper.LoginCancelled):
            manual.run()
        self.assert_old()
        self.check.assert_not_called()

    def test_browser_close_during_wait_is_cancellation_not_login_failure(self):
        from playwright.sync_api import Error
        def closed(_):
            self.context.pages = []
            raise Error("Target closed")
        self.page.wait_for_timeout.side_effect = closed
        with self.assertRaises(helper.LoginCancelled):
            manual.run()
        self.assert_old()

    def test_authenticated_spa_does_not_have_to_change_login_url(self):
        self.page.url = "https://login.example.test/login"
        self.assertEqual(manual.run(), 0)
        self.check.assert_called_once_with(self.cookies)

    def test_expired_candidate_is_not_polled_repeatedly_and_cancel_preserves_old(self):
        self.check.side_effect = auth.ManualLoginRequired("未登录")
        calls = []
        def wait(_):
            calls.append(1)
            if len(calls) == 2:
                (self.folder / "stop").touch()
        self.page.wait_for_timeout.side_effect = wait
        with self.assertRaises(helper.LoginCancelled):
            manual.run()
        self.check.assert_called_once()
        self.assertEqual(self.page.goto.call_args.args[0], auth.pms_config.LOGIN_URL)
        self.context.clear_cookies.assert_not_called()
        self.assert_old()

    def test_blank_config_does_not_compare_previous_hotel(self):
        self.settings["pms"]["hotel_name"] = ""
        self.path.write_text(json.dumps({"url": auth.pms_config.LOGIN_BASE_URL, "hotel_name": "旧酒店"}), encoding="utf-8")
        self.assertEqual(manual.run(), 0)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["hotel_name"], "测试酒店")

    def test_cancellation_during_identity_check_does_not_save(self):
        def verified(_):
            (self.folder / "stop").touch()
            return "测试酒店"
        self.check.side_effect = verified
        with self.assertRaises(helper.LoginCancelled):
            manual.run()
        self.assert_old()

    def test_configuration_write_failure_preserves_old_cookie(self):
        with patch.object(runner, "save_json", side_effect=OSError("fake")):
            with self.assertRaises(OSError):
                manual.run()
        self.assert_old()

    def test_frontend_places_manual_button_next_to_address_without_dropdown(self):
        with patch.object(helper, "read_status", return_value={}):
            html = panel.config_control(self.settings, "pms.login_base_url", "登录地址", False)
        self.assertIn("pms-login-input", html)
        self.assertIn("formaction='/platform-login/pms'>手动登录", html)
        self.assertIn("切回账号密码", html)
        self.assertNotIn("<select", html)
        self.assertNotIn("打开此地址", html)
        self.assertNotIn("切换账号", html)
        self.assertNotIn("fake", html)

    def test_manual_route_saves_mode_but_never_requires_password_or_runs_collection(self):
        with patch.object(panel, "apply_form_to_settings", side_effect=lambda value: value), \
             patch.object(runner, "save_json") as save, patch.object(helper, "start") as start:
            response = panel.app.test_client().post("/platform-login/pms")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(save.call_args.args[1]["pms"]["login_mode"], "manual")
        start.assert_called_once_with("pms", self.settings, switch_account=False)

    def test_success_status_returns_saved_name_and_completion_marker(self):
        state = {"status": "success", "completed_at": "2026-10-26 20:22:45", "cookie": "PRIVATE"}
        with patch.object(helper, "read_status", return_value=state):
            data = panel.app.test_client().get("/api/platform-login/pms").get_json()
            html = manual.login_control(self.settings)
        self.assertEqual(data["hotel_name"], "测试酒店")
        self.assertNotIn("cookie", data)
        self.assertIn("data-completed-at='2026-10-26 20:22:45'", html)

    def test_failed_or_pending_status_does_not_return_name_for_backfill(self):
        for status in ("failed", "waiting", "cancelled", "starting"):
            with patch.object(helper, "read_status", return_value={"status": status}):
                data = panel.app.test_client().get("/api/platform-login/pms").get_json()
            self.assertEqual(data["hotel_name"], "")

    def test_return_to_password_mode_closes_helper_without_starting_browser(self):
        with patch.object(panel, "apply_form_to_settings", side_effect=lambda value: value), \
             patch.object(runner, "save_json"), patch.object(helper, "cancel", return_value=True) as cancel, \
             patch.object(helper, "start") as start:
            panel.app.test_client().post("/platform-login/pms", data={"login_mode": "password"})
        self.assertEqual(self.settings["pms"]["login_mode"], "password")
        start.assert_not_called()
        cancel.assert_called_once_with("pms")
        self.assert_old()

    def test_manual_helper_does_not_run_for_other_provider(self):
        self.settings["pms"]["provider"] = "bypms"
        with self.assertRaisesRegex(RuntimeError, "别样红"):
            manual.run()
        self.launch.assert_not_called()

    def test_mode_validation_and_old_settings_compatibility(self):
        self.assertEqual(validate_settings(self.settings), [])
        self.settings["pms"].pop("login_mode")
        self.assertEqual(validate_settings(self.settings), [])
        self.settings["pms"]["login_mode"] = "unknown"
        self.assertTrue(any("login_mode" in e for e in validate_settings(self.settings)))


if __name__ == "__main__":
    unittest.main()
