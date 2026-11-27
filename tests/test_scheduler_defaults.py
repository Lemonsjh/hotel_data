import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import call, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "OTA采集服务"))

import price_scheduler
import promotion_scheduler
from settings_validation import require_valid_settings


class SchedulerDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT)))
        self.now = datetime(2026, 11, 27, 10)

    def scheduler_paths(self):
        return dict(
            STATE_DIR=self.directory, STATUS_PATH=self.directory / "status.json",
            PID_PATH=self.directory / "scheduler.pid", STOP_PATH=self.directory / "scheduler.stop",
        )

    def test_price_uses_defaults_and_preserves_explicit_values(self):
        config_path = self.directory / "settings.json"
        executor_path = self.directory / "executor.py"
        executor_path.touch()
        for config, seconds, max_tasks, minutes in (
            ({}, 60, 20, 5),
            (dict(startup_delay_seconds="", max_tasks_per_run="", interval_minutes=""), 60, 20, 5),
            (dict(startup_delay_seconds=None, max_tasks_per_run=None, interval_minutes=None), 60, 20, 5),
            (dict(startup_delay_seconds=0, max_tasks_per_run=4, interval_minutes=7), 0, 4, 7),
        ):
            with self.subTest(config=config):
                config_path.write_text(json.dumps({"price_scheduler": config}), encoding="utf-8")
                with (
                    patch.multiple(price_scheduler, **self.scheduler_paths(),
                                   CONFIG_PATH=config_path, EXECUTOR_PATH=executor_path),
                    patch.object(price_scheduler, "datetime", wraps=datetime) as clock,
                    patch.object(price_scheduler, "run_executor", return_value=0) as execute,
                    patch.object(price_scheduler, "wait_until", side_effect=[False, True]) as wait,
                ):
                    clock.now.return_value = self.now
                    self.assertEqual(price_scheduler.run_scheduler(), 0)
                    execute.assert_called_once_with(max_tasks)
                    self.assertEqual(wait.call_args_list, [
                        call(self.now + timedelta(seconds=seconds)),
                        call(self.now + timedelta(minutes=minutes)),
                    ])
                    self.assertFalse(price_scheduler.PID_PATH.exists())

    def test_promotion_uses_defaults_and_preserves_explicit_values(self):
        for config, price_config, max_tasks, minutes in (
            ({}, {}, 5, 5),
            ({"max_tasks_per_run": ""}, {"interval_minutes": ""}, 5, 5),
            ({"max_tasks_per_run": None}, {"interval_minutes": None}, 5, 5),
            ({"max_tasks_per_run": 3}, {"interval_minutes": 7}, 3, 7),
        ):
            with self.subTest(config=config, price_config=price_config):
                settings = require_valid_settings({
                    "promotion_scheduler": {"enabled": True, **config},
                    "price_scheduler": price_config,
                })
                with (
                    patch.multiple(promotion_scheduler, **self.scheduler_paths()),
                    patch.object(promotion_scheduler.runner, "load_settings", return_value=settings),
                    patch.object(promotion_scheduler, "datetime", wraps=datetime) as clock,
                    patch.object(promotion_scheduler, "run_executor", return_value=0) as execute,
                    patch.object(promotion_scheduler, "wait_until", return_value=True) as wait,
                ):
                    clock.now.return_value = self.now
                    self.assertEqual(promotion_scheduler.run_scheduler(), 0)
                    execute.assert_called_once_with(settings, max_tasks)
                    wait.assert_called_once_with(self.now + timedelta(minutes=minutes))
                    self.assertFalse(promotion_scheduler.PID_PATH.exists())
