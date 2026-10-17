from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "OTA采集服务"))

import local_reset
import panel
import runner


class LocalResetTests(unittest.TestCase):
    def assert_schedulers_paused(self, settings):
        self.assertIs(settings["service"]["scheduler_enabled"], False)
        for section in ("price_scheduler", "reply_scheduler", "promotion_scheduler"):
            self.assertIs(settings[section]["enabled"], False)

    def test_example_selects_all_current_tasks_without_starting_schedulers(self):
        settings = runner.load_json(local_reset.DEFAULT_SETTINGS_PATH, {})
        self.assertEqual(settings["tasks"], dict.fromkeys(runner.TASKS, True))
        self.assert_schedulers_paused(settings)

    def test_reset_defaults_select_all_tasks_even_with_old_disabled_template(self):
        template = {
            "tasks": {"meituan_business": False, "meituan_exposure_source": True},
            "service": {"scheduler_enabled": True},
            "price_scheduler": {"enabled": True},
            "reply_scheduler": {"enabled": True},
            "promotion_scheduler": {"enabled": True},
        }
        with patch.object(runner, "load_json", return_value=copy.deepcopy(template)), \
             patch.dict(runner.TASKS, {"future_task": ("pms", "example.py", [])}):
            settings = local_reset.default_settings()
            self.assertEqual(settings["tasks"], dict.fromkeys(runner.TASKS, True))
        self.assert_schedulers_paused(settings)
        self.assertNotIn("meituan_exposure_source", settings["tasks"])

    def test_reset_saves_checked_tasks_and_keeps_schedulers_paused(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            config = Path(directory) / "settings.json"
            with patch.object(runner, "CONFIG_PATH", config), \
                 patch.object(local_reset, "is_collection_running", return_value=False), \
                 patch.object(local_reset, "request_scheduler_stop") as stop, \
                 patch.object(local_reset, "stop_login_helpers") as helpers, \
                 patch.object(local_reset, "clear_login_sessions") as sessions:
                local_reset.reset_local_configuration()
            settings = runner.load_json(config, {})
        stop.assert_called_once_with()
        helpers.assert_called_once_with()
        sessions.assert_called_once_with()
        self.assertEqual(settings["tasks"], dict.fromkeys(runner.TASKS, True))
        self.assert_schedulers_paused(settings)

    def test_active_collection_still_blocks_reset_without_side_effects(self):
        with patch.object(local_reset, "is_collection_running", return_value=True), \
             patch.object(local_reset, "request_scheduler_stop") as stop, \
             patch.object(local_reset, "stop_login_helpers") as helpers, \
             patch.object(local_reset, "clear_login_sessions") as sessions, \
             patch.object(runner, "save_json") as save:
            with self.assertRaises(RuntimeError):
                local_reset.reset_local_configuration()
        for action in (stop, helpers, sessions, save):
            action.assert_not_called()

    def test_normal_load_preserves_explicitly_disabled_tasks(self):
        settings = local_reset.default_settings()
        settings["tasks"] = dict.fromkeys(runner.TASKS, False)
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            config = Path(directory) / "settings.json"
            runner.save_json(config, settings)
            with patch.object(runner, "CONFIG_PATH", config):
                loaded = runner.load_settings()
        self.assertEqual(loaded["tasks"], dict.fromkeys(runner.TASKS, False))
        self.assert_schedulers_paused(loaded)

    def test_config_page_renders_reset_tasks_as_checked(self):
        settings = local_reset.default_settings()
        with patch.object(runner, "load_settings", return_value=settings), \
             patch.object(panel, "config_section", return_value=""):
            response = panel.app.test_client().get("/config")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for name in runner.TASKS:
            self.assertIn(f"name='task.{name}' checked", html)


if __name__ == "__main__":
    unittest.main()
