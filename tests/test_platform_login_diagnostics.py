from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "OTA采集服务"))
import platform_login as login
if __package__:
    from .test_login_diagnostics import FakeContext, FakeRequest
else:
    from test_login_diagnostics import FakeContext, FakeRequest


class PlatformLoginDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix=".test_login_flow_", dir=ROOT)))
        self.context = FakeContext()
        self.context.close = lambda: None
        self.context.clear_cookies = lambda: None
        self.options = []

        def launch(**options):
            self.options.append(options)
            return self.context

        playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
        patches = {
            "sync_playwright": lambda: nullcontext(playwright), "browser_profile_lock": lambda **kw: nullcontext(),
            "stop_path": lambda platform: self.folder / "stop", "write_status": None,
            "read_status": {"pid": 0}, "stop_profile_edge_processes": None,
            "cookie_names": {"mebsid", "ebbsid", "usertoken", "usersign"},
            "wait_for_auth": self.context.pages[0], "validate_protected_page": None,
            "save_cookies": 2, "show_success": None, "keep_open": None,
            "refresh_hotel_settings": {"ok": True},
        }
        self.mocks = {}
        for name, value in patches.items():
            args = {"side_effect": value} if callable(value) else {"return_value": value}
            self.mocks[name] = self.stack.enter_context(patch.object(login, name, **args))
        self.stack.enter_context(patch.object(login.runner, "LOG_DIR", self.folder))
        self.stack.enter_context(patch.object(login.time, "sleep", return_value=None))
        self.navigate = self.stack.enter_context(patch.object(login, "navigate", side_effect=self.observed_navigation))

    def observed_navigation(self, page, url):
        page.url = url
        page.fire("domcontentloaded")
        request = FakeRequest()
        self.context.fire("request", request)
        self.context.fire("response", request.reply)
        self.context.fire("requestfinished", request)

    def records(self):
        files = list(self.folder.glob("*meituan_login_diagnostic*.log"))
        self.assertEqual(len(files), 1)
        text = files[0].read_text(encoding="utf-8")
        self.assertNotIn("PRIVATE", text)
        return [json.loads(line) for line in text.splitlines()]

    def test_success_preserves_navigation_and_cookie_save_behavior(self):
        self.assertEqual(login.run("meituan"), 0)
        self.assertEqual(self.records()[-1]["outcome"], "success")
        self.assertEqual([call.args[1] for call in self.navigate.call_args_list], [
            login.PLATFORMS["meituan"]["url"], login.PLATFORMS["meituan"]["eb_url"],
        ])
        self.assertEqual([r["phase"] for r in self.records() if r["event"] == "phase"],
                         ["opening_login", "waiting_main", "opening_eb", "validating", "saving"])
        self.mocks["save_cookies"].assert_called_once_with("meituan", self.context)
        self.assertEqual(self.options[0]["args"], ["--start-maximized"])
        self.assertFalse(self.options[0]["headless"])
        self.assertNotIn("ignore_default_args", self.options[0])

    def test_validation_navigates_when_auth_returns_a_different_page(self):
        def authenticated(*args):
            page = self.context.pages[0]
            page.url = login.PLATFORMS["meituan"]["url"]
            return page
        self.mocks["wait_for_auth"].side_effect = authenticated
        self.assertEqual(login.run("meituan"), 0)
        self.assertEqual(self.navigate.call_count, 3)
        self.mocks["validate_protected_page"].assert_called_once()
        self.mocks["save_cookies"].assert_called_once_with("meituan", self.context)
        self.assertEqual(self.records()[-1]["outcome"], "success")

    def test_login_failure_records_failure_without_saving_any_cookies(self):
        self.navigate.side_effect = RuntimeError("PRIVATE")
        self.assertEqual(login.run("meituan", switch_account=True), 2)
        self.assertEqual(self.records()[-1]["outcome"], "failed")
        self.mocks["save_cookies"].assert_not_called()
        self.mocks["refresh_hotel_settings"].assert_not_called()
        self.assertEqual(self.navigate.call_count, 1)  # 不添加登录重试。

    def test_cancellation_is_not_reported_as_authenticated(self):
        self.mocks["wait_for_auth"].side_effect = login.LoginCancelled("用户关闭窗口")
        self.assertEqual(login.run("meituan"), 1)
        self.assertEqual(self.records()[-1]["outcome"], "cancelled")
        self.mocks["save_cookies"].assert_not_called()

    def test_other_platform_login_has_no_diagnostics(self):
        self.assertEqual(login.run("ctrip"), 0)
        self.assertEqual(list(self.folder.glob("*.log")), [])
        self.mocks["save_cookies"].assert_called_once_with("ctrip", self.context)

    def test_log_creation_failure_does_not_change_login_success(self):
        with patch("login_diagnostics.Path.open", side_effect=OSError("日志目录不可写 PRIVATE")):
            self.assertEqual(login.run("meituan"), 0)
        self.mocks["save_cookies"].assert_called_once_with("meituan", self.context)
        self.assertEqual(self.navigate.call_count, 2)


if __name__ == "__main__":
    unittest.main()
