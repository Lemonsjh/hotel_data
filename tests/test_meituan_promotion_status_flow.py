"""促销状态串行执行、冷却及浏览器清理，不访问线上或真实数据库。"""
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, nullcontext, redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "美团OTA数据采集代码"))
import meituan_promotion_status_data as task
import meituan_status_session as session


class PromotionStatusFlowTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir=ROOT)))
        self.state_path = self.root / "schedule.json"
        self.stack.enter_context(patch.object(task, "schedule_state_path", return_value=self.state_path))
        self.stack.enter_context(patch.object(task, "OUTPUT_DIR", self.root))
        self.stack.enter_context(patch.dict(os.environ, {"HOTEL_ID": "test"}))
        self.stack.enter_context(patch.object(sys, "argv", ["promotion-status"]))
        self.stack.enter_context(patch.object(task, "browser_profile_lock", return_value=nullcontext()))
        self.calls = []
        self.checks = {}
        for name in ("youmeihui", "business_travel", "public_welfare", "scheduled_invoice",
                     "hourly_room", "hotel_highlights", "auto_order"):
            def perform(name=name):
                self.calls.append(name)
                return "OPEN"
            self.checks[name] = self.stack.enter_context(patch.object(task, f"fetch_{name}_status", side_effect=perform))
        self.save = self.stack.enter_context(patch.object(task, "save_status"))
        self.out = StringIO()
        self.stack.enter_context(redirect_stdout(self.out))

    def test_success_writes_all_seven_once_and_logs_durations(self):
        self.assertEqual(task.main(), 0)
        self.assertEqual(len(self.calls), 7)
        self.assertEqual(self.save.call_count, 7)
        self.assertEqual(self.out.getvalue().count("check started"), 7)
        self.assertEqual(self.out.getvalue().count("duration="), 7)
        state = task.load_schedule_state()
        self.assertEqual(state["cooldown_reason"], "success")
        self.assertIn("last_success_at", state)
        self.assertGreaterEqual(state["cooldown_hours"], 10)
        self.assertLessEqual(state["cooldown_hours"], 14)

    def test_unknown_welfare_is_failure_without_inserting_initial_closed(self):
        self.checks["public_welfare"].side_effect = RuntimeError("未返回明确的开通状态")
        self.assertEqual(task.main(), 1)
        self.assertEqual(self.save.call_count, 6)
        self.assertNotIn("public_welfare_traffic", [call.args[1] for call in self.save.call_args_list])
        self.assertIn("auto_order", self.calls)
        result = json.loads((self.root / "meituan_ota_promotion_status.json").read_text(encoding="utf-8"))
        self.assertEqual(len(result["items"]), 6)
        self.assertEqual(result["failures"][0]["promotion_code"], "public_welfare_traffic")
        self.assertIn("已确认6/7项", self.out.getvalue())
        state = task.load_schedule_state()
        self.assertEqual(state["cooldown_reason"], "failure")
        self.assertNotIn("last_success_at", state)

    def test_database_write_failure_is_reported_and_does_not_stop_later_items(self):
        self.save.side_effect = [RuntimeError("write failed"), *([None] * 6)]
        self.assertEqual(task.main(), 1)
        self.assertEqual(len(self.calls), 7)
        self.assertEqual(task.load_schedule_state()["cooldown_reason"], "failure")

    def test_cooldown_skip_does_not_run_or_refresh_success_or_failure(self):
        for reason in ("success", "failure"):
            state = {"cooldown_reason": reason, "next_allowed_at": (datetime.now() + timedelta(hours=2)).isoformat(),
                     "last_attempt_at": "unchanged", "cookie_fingerprint": "old-cookie"}
            self.state_path.write_text(json.dumps(state), encoding="utf-8")
            self.assertEqual(task.main(), 0)
            self.assertEqual(task.load_schedule_state(), state)
        self.assertFalse(self.calls)
        self.save.assert_not_called()

    def test_force_bypasses_cooldown_but_generates_new_one_on_failure(self):
        self.state_path.write_text(json.dumps({"cooldown_reason": "success",
            "next_allowed_at": (datetime.now() + timedelta(hours=2)).isoformat()}), encoding="utf-8")
        self.checks["scheduled_invoice"].side_effect = RuntimeError("invalid invoice data")
        with patch.object(sys, "argv", ["promotion-status", "--force"]):
            self.assertEqual(task.main(), 1)
        self.assertEqual(self.save.call_count, 6)
        self.assertEqual(task.load_schedule_state()["cooldown_reason"], "failure")


class StatusSessionTests(unittest.TestCase):
    def test_context_and_browser_close_on_success_and_navigation_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail), patch.object(session, "sync_playwright") as factory, \
                 patch.object(session, "launch_status_context") as launch:
                context = launch.return_value
                if fail:
                    context.new_page.return_value.goto.side_effect = RuntimeError("navigation failed")
                    with self.assertRaises(RuntimeError):
                        with session.status_page("https://example.test"):
                            pass
                else:
                    with session.status_page("https://example.test") as page:
                        self.assertIs(page, context.new_page.return_value)
                context.close.assert_called_once()
                context.browser.close.assert_called_once()
                launch.assert_called_once_with(factory.return_value.__enter__.return_value)

    def test_missing_backend_cookie_fails_without_starting_browser(self):
        with patch.object(session, "load_login_session", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "缺少公益流量会话"):
                session.require_backend_session("", "公益流量")
        with patch.object(session, "load_login_session", return_value={"cookies": []}):
            session.require_backend_session("", "公益流量")


if __name__ == "__main__":
    unittest.main()
