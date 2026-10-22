"""业务源码中展示修正的回归，不读取本机配置或启动调度。"""
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "OTA采集服务"))
import runner
import status_routes


class SourceSyncDisplayTests(unittest.TestCase):
    def test_disabled_ota_platforms_do_not_warn_about_unused_paths_or_cookies(self):
        settings = {"pms": {"enabled": False}, "mysql": {"password": "fake"},
                    "meituan": {"enabled": False}, "ctrip": {"enabled": False}}
        missing = Path("missing-code-directory")
        with patch.object(runner, "python_path", return_value=Path(sys.executable)), \
             patch.object(runner, "project_path", return_value=missing):
            self.assertEqual(runner.config_warnings(settings), [])
            settings["meituan"]["enabled"] = True
            warnings = runner.config_warnings(settings)
        self.assertTrue(any("MEITUAN_ME_COOKIE" in warning for warning in warnings))
        self.assertTrue(any("meituan code dir" in warning for warning in warnings))
        self.assertFalse(any("ctrip" in warning.lower() for warning in warnings))

    def test_foxhis_connection_warnings_are_not_removed(self):
        settings = {"pms": {"enabled": False}, "foxhis": {"enabled": True},
                    "meituan": {"enabled": False}, "ctrip": {"enabled": False}}
        warnings = runner.config_warnings(settings)
        self.assertTrue(any("Foxhis server" in warning for warning in warnings))
        self.assertTrue(any("Foxhis username" in warning for warning in warnings))

    def test_waiting_scheduler_refreshes_slowly_and_running_refreshes_quickly(self):
        app = Flask(__name__)
        status_routes.register(app)
        for state, scheduler, delay in (
            ("success", {"alive": True, "scheduler_status": "waiting"}, "15000"),
            ("running", {"alive": True, "scheduler_status": "collecting"}, "2000"),
            ("success", {"alive": False, "scheduler_status": "stopped"}, None),
        ):
            with self.subTest(state=state, scheduler=scheduler), ExitStack() as stack:
                stack.enter_context(patch.object(runner, "load_settings", return_value={}))
                stack.enter_context(patch.object(runner, "reconcile_stale_run", return_value={"last_run_status": state}))
                stack.enter_context(patch.object(runner, "config_warnings", return_value=[]))
                stack.enter_context(patch.object(status_routes, "manual_scheduler_status", return_value=scheduler))
                for name in ("price_scheduler_status", "reply_scheduler_status", "promotion_scheduler_status"):
                    stack.enter_context(patch.object(status_routes, name, return_value={}))
                stack.enter_context(patch.object(status_routes, "page", side_effect=lambda title, body, *args, **kwargs: body))
                response = app.test_client().get("/")
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            if delay:
                self.assertIn(f"location.reload(), {delay}", html)
            else:
                self.assertNotIn("location.reload()", html)


if __name__ == "__main__":
    unittest.main()
