from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
PMS = ROOT / "别样红PMS数据采集代码" / "PMS登录"
sys.path[:0] = [str(ROOT / "OTA采集服务"), str(PMS / "scripts")]
import pms_utils as auth
import pms_utils as utils
import runner


class ManualAuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "session.json"
        self.addCleanup(patch.stopall)
        patch.object(utils, "SESSION_PATH", self.path).start()
        patch.dict(os.environ, {"HOTEL_ID": "test", "PMS_HOTEL_NAME": "测试酒店"}).start()
        self.cookies = dict.fromkeys(utils.REQUIRED_COOKIES, "fake-cookie")
        self.session = {"url": auth.pms_config.LOGIN_BASE_URL, "cookies": self.cookies,
                        "hotel_name": "测试酒店", "hotel_id": "test", "login_mode": "manual",
                        "account_fingerprint": utils.account_fingerprint("account"),
                        "login_time": str(datetime.now() - timedelta(days=10))}
        self.path.write_text(json.dumps(self.session), encoding="utf-8")

    def response(self, status=200, payload=None, headers=None):
        return Mock(status_code=status, headers=headers or {}, json=Mock(return_value=payload or {
            "code": 0, "data": {"loginOrgName": "测试酒店"}}))

    def test_old_manual_session_uses_server_check_not_twelve_hour_expiry(self):
        with patch.object(auth.requests, "post", return_value=self.response()) as post:
            self.assertEqual(auth.require_manual_session("account"), "测试酒店")
        post.assert_called_once()
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertEqual(post.call_args.kwargs["cookies"], self.cookies)
        self.assertEqual(json.loads(self.path.read_text()), self.session)

    def test_network_failure_does_not_expire_or_replace_old_session(self):
        for error in (auth.requests.Timeout("PRIVATE"), auth.requests.ConnectionError("PRIVATE")):
            with self.subTest(error=type(error).__name__), patch.object(auth.requests, "post", side_effect=error):
                with self.assertRaises(auth.SessionCheckError) as raised:
                    auth.require_manual_session("account")
                self.assertNotIsInstance(raised.exception, auth.ManualLoginRequired)
                self.assertNotIn("PRIVATE", str(raised.exception))
                self.assertEqual(json.loads(self.path.read_text()), self.session)

    def test_explicit_expiry_and_login_redirect_require_human_login(self):
        for response in (self.response(401), self.response(302, headers={"Location": "/login"})):
            with patch.object(auth.requests, "post", return_value=response):
                with self.assertRaises(auth.ManualLoginRequired):
                    auth.require_manual_session("account")
                self.assertTrue(self.path.exists())

    def test_unknown_permission_server_and_payload_failures_are_not_expiry(self):
        replies = [self.response(403), self.response(503), self.response(payload={"data": {}}),
                   self.response(payload={"code": 500, "data": {"loginOrgName": "测试酒店"}}),
                   self.response(payload={"success": False}), self.response(payload={"code": True})]
        bad_json = self.response()
        bad_json.json.side_effect = ValueError("PRIVATE")
        for response in replies + [bad_json]:
            with self.subTest(response=response), patch.object(auth.requests, "post", return_value=response):
                with self.assertRaises(auth.SessionCheckError) as raised:
                    auth.require_manual_session("account")
                self.assertNotIsInstance(raised.exception, auth.ManualLoginRequired)
                self.assertEqual(json.loads(self.path.read_text()), self.session)

    def test_actual_hotel_does_not_have_to_match_previous_display_name(self):
        with patch.object(auth.requests, "post", return_value=self.response(payload={
            "data": {"loginOrgName": "其他酒店"}})):
            self.assertEqual(auth.require_manual_session("account"), "其他酒店")
        self.assertEqual(json.loads(self.path.read_text()), self.session)

    def test_shared_name_lookup_preserves_legacy_permissive_behavior(self):
        reply = self.response(payload={"code": 1000, "data": {"loginOrgName": "旧模式酒店"}})
        with patch.object(utils.requests, "post", return_value=reply):
            self.assertEqual(utils.fetch_hotel_name(self.cookies), "旧模式酒店")
            with self.assertRaises(utils.SessionCheckError):
                utils.check_identity(self.cookies)
        with patch.object(utils.requests, "post", side_effect=utils.requests.Timeout("PRIVATE")):
            self.assertEqual(utils.fetch_hotel_name(self.cookies), "")


    def test_changed_account_server_or_database_identity_requires_new_login_before_network(self):
        for field, value in (("url", "https://other.test:8101"), ("hotel_id", "other"),
                             ("report_base_url", "https://other.test:8081"), ("account_fingerprint", "other")):
            with self.subTest(field=field), patch.object(auth.requests, "post") as post:
                self.path.write_text(json.dumps({**self.session, field: value}), encoding="utf-8")
                with self.assertRaises(auth.ManualLoginRequired):
                    auth.require_manual_session("account")
                post.assert_not_called()

    def test_empty_credentials_are_supported_and_no_session_does_not_send_code(self):
        with patch.object(auth.requests, "post", return_value=self.response()) as post:
            self.assertEqual(auth.require_manual_session(), "测试酒店")
            self.path.unlink()
            with self.assertRaises(auth.ManualLoginRequired):
                auth.require_manual_session()
            self.assertEqual(post.call_count, 1)

    def test_cookie_scope_filter_does_not_merge_unrelated_pages(self):
        context = Mock()
        context.cookies.return_value = [{"name": "Token", "value": "fake"}]
        self.assertEqual(auth.browser_cookies(context), {"Token": "fake"})
        self.assertEqual(context.cookies.call_args.args[0],
                         [f"{utils.SERVICE_API_BASE_URL}/api/v1/fox/user/checkLogin"])

    def test_duplicate_cookie_names_choose_specific_domain_and_path(self):
        context = Mock()
        context.cookies.return_value = [
            {"name": "Token", "value": "api", "domain": "hotel.example.test", "path": "/api"},
            {"name": "Token", "value": "root", "domain": "hotel.example.test", "path": "/"},
            {"name": "Token", "value": "parent", "domain": ".example.test", "path": "/"},
            {"name": "SessionId", "value": "host", "domain": "hotel.example.test", "path": "/"},
            {"name": "SessionId", "value": "domain", "domain": ".hotel.example.test", "path": "/"},
            {"name": "analytics", "value": "one", "domain": ".example.test", "path": "/"},
            {"name": "analytics", "value": "two", "domain": "hotel.example.test", "path": "/"},
        ]
        self.assertEqual(auth.browser_cookies(context), {"Token": "api", "SessionId": "host", "analytics": "two"})

    def test_runner_mode_defaults_and_manual_does_not_require_password(self):
        settings = {"pms": {"enabled": True, "login_mode": "manual"}, "paths": {}}
        with patch.dict(os.environ, {"PMS_USERNAME": "old", "PMS_PASSWORD": "old"}):
            env = runner.build_env(settings, "pms")
        self.assertEqual(env["PMS_LOGIN_MODE"], "manual")
        self.assertEqual(env["PMS_USERNAME"], "")
        self.assertEqual(env["PMS_PASSWORD"], "")
        warnings = runner.config_warnings(settings)
        self.assertNotIn("PMS username is empty.", warnings)
        self.assertNotIn("PMS password is empty.", warnings)
        settings["pms"].pop("login_mode")
        self.assertEqual(runner.build_env(settings, "pms")["PMS_LOGIN_MODE"], "password")
        self.assertIn("PMS password is empty.", runner.config_warnings(settings))

    def test_collector_manual_gate_stops_before_reports_without_automatic_login(self):
        spec = importlib.util.spec_from_file_location("manual_fetch_main_test", PMS / "fetch_main.py")
        target = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(target)
        with patch.dict(os.environ, {"PMS_LOGIN_MODE": "manual", "PMS_PASSWORD": ""}), \
             patch.object(sys, "argv", ["fetch_main.py"]), \
             patch.object(auth, "require_manual_session", side_effect=auth.ManualLoginRequired("请手动登录")), \
             patch.object(target, "need_login") as automatic, \
             patch.object(target, "run_fetch_jobs") as fetch:
            self.assertEqual(target.main(), 1)
        automatic.assert_not_called()
        fetch.assert_not_called()
        self.assertTrue(self.path.exists())

    def test_force_in_manual_mode_preserves_old_session_without_opening_browser(self):
        spec = importlib.util.spec_from_file_location("manual_force_fetch_test", PMS / "fetch_main.py")
        target = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(target)
        with patch.dict(os.environ, {"PMS_LOGIN_MODE": "manual"}), \
             patch.object(sys, "argv", ["fetch_main.py", "--login"]), \
             patch.object(auth, "require_manual_session") as check:
            self.assertEqual(target.main(), 1)
        check.assert_not_called()
        self.assertEqual(json.loads(self.path.read_text()), self.session)

    def test_password_mode_keeps_twelve_hour_refresh_and_replaces_manual_mode_on_return(self):
        spec = importlib.util.spec_from_file_location("password_fetch_test", PMS / "fetch_main.py")
        target = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(target)
        self.assertTrue(target.need_login(username="account"))
        self.session.pop("login_mode")
        self.session["login_time"] = (datetime.now() - timedelta(hours=13)).strftime("%Y-%m-%d %H:%M:%S")
        self.path.write_text(json.dumps(self.session), encoding="utf-8")
        self.assertTrue(target.need_login(username="account"))
        self.session["login_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.path.write_text(json.dumps(self.session), encoding="utf-8")
        self.assertFalse(target.need_login(username="account"))


if __name__ == "__main__":
    unittest.main()
