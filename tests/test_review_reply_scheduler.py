import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "OTA采集服务"))

import review_reply_routes as routes
import review_reply_scheduler as scheduler


class ReviewReplySchedulerTests(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT)))
        self.enterContext(patch.multiple(
            scheduler, STATE_DIR=directory, STATUS_PATH=directory / "status.json",
            PID_PATH=directory / "scheduler.pid", STOP_PATH=directory / "scheduler.stop",
        ))
        self.settings = {}
        self.enterContext(patch.object(scheduler.runner, "load_settings", return_value=self.settings))

    def test_start_recovers_invalid_status_file(self):
        for contents in (b"", b'{"pid":', b"[]", b"\xff"):
            with self.subTest(contents=contents):
                scheduler.STATUS_PATH.write_bytes(contents)
                self.assertFalse(routes.reply_scheduler_status()["alive"])
                self.assertEqual(scheduler.run_scheduler(), 0)
                self.assertEqual(scheduler.load_status()["scheduler_status"], "stopped")
                self.assertFalse(scheduler.PID_PATH.exists())

    def test_startup_error_releases_pid(self):
        with patch.object(scheduler, "save_status", side_effect=PermissionError("status is locked")):
            with self.assertRaisesRegex(PermissionError, "status is locked"):
                scheduler.run_scheduler()
        self.assertFalse(scheduler.PID_PATH.exists())

    def test_reply_limits_allow_blank_config_and_preserve_set_values(self):
        for limit, interval, expected_limit, expected_minute in (
            ("", "", 10, 5), (None, None, 10, 5), (3, 7, 3, 7),
        ):
            with self.subTest(limit=limit, interval=interval):
                self.settings.update(
                    reply_scheduler={"enabled": True, "max_tasks_per_run": limit},
                    price_scheduler={"interval_minutes": interval},
                )
                with patch.object(scheduler, "run_executor", return_value=0) as execute, \
                     patch.object(scheduler, "wait_until", return_value=True) as wait, \
                     patch.object(scheduler, "datetime", wraps=datetime) as clock:
                    clock.now.return_value = datetime(2026, 11, 27, 10)
                    self.assertEqual(scheduler.run_scheduler(), 0)
                    execute.assert_called_once_with(self.settings, expected_limit)
                    wait.assert_called_once_with(datetime(2026, 11, 27, 10, expected_minute))
