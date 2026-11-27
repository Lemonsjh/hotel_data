import sys
import tempfile
import unittest
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
        self.enterContext(patch.object(scheduler.runner, "load_settings", return_value={}))

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
