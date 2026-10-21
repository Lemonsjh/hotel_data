"""日结去重：按成功落库记录跳过，实时数据继续刷新。"""
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "OTA采集服务/foxhis_pms"))
import foxhis_common as common


class ReportCadenceTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory(prefix="foxhis_cadence_", dir=Path(__file__).resolve().parents[2]))
        self.config = common.Config(server="FoxFront", database="foxhis1", username="reader", hotel_id="TEST", hotel_name="测试酒店",
                                    output_dir=directory, fetch_after="03:30",
                                    mysql={"host": "target", "port": 3306, "database": "hotel_test"})
        self.clock = self.stack.enter_context(patch.object(common, "datetime", wraps=datetime))
        self.clock.now.return_value = datetime(2026, 10, 19, 4)
        self.stack.enter_context(patch.object(common.Config, "from_env", return_value=self.config))
        self.endpoint = self.stack.enter_context(patch.object(common.Config, "endpoint", return_value=("source", 5000)))
        self.reachable = self.stack.enter_context(patch.object(common, "check_reachable", return_value=(True, "ok")))
        self.stack.enter_context(patch.dict(os.environ, {"FOXHIS_BUSINESS_DATE": ""}))
        self.collect = Mock(return_value={"row_count": 1, "written": 1, "dry_run": False})
        self.state_path = Path(directory) / common.DAILY_STATE_FILE

    def run_report(self, key="jy01", *args, realtime=False):
        with patch.object(sys, "argv", ["collector", *args]), redirect_stdout(StringIO()):
            return common.report_main(key, self.collect, realtime=realtime)

    def test_daily_success_skips_source_access_and_persists_across_invocations(self):
        self.run_report()
        self.run_report()
        self.assertEqual(self.collect.call_count, 1)
        self.assertEqual(self.reachable.call_count, 1)
        self.assertEqual(json.loads(self.state_path.read_text())["jy01"]["business_date"], "2026-10-18")
        self.clock.now.return_value = datetime(2026, 10, 20, 4)
        self.run_report()
        self.assertEqual(self.collect.call_count, 2)

    def test_each_daily_report_has_its_own_marker(self):
        for key in sorted(common.DAILY_REPORTS):
            self.run_report(key)
            self.run_report(key)
        self.assertEqual(self.collect.call_count, 4)

    def test_failures_and_zero_writes_remain_retryable(self):
        self.collect.side_effect = [RuntimeError("write failed"), {"written": 0}, {"written": 1}]
        with self.assertRaises(RuntimeError):
            self.run_report()
        self.assertFalse(self.state_path.exists())
        self.run_report()
        self.assertFalse(self.state_path.exists())
        self.run_report()
        self.assertTrue(self.state_path.exists())

    def test_dry_run_bypasses_marker_without_recording_success(self):
        self.run_report("jy01", "--dry-run")
        self.assertFalse(self.state_path.exists())
        self.run_report()
        self.run_report("jy01", "--dry-run")
        self.assertEqual(self.collect.call_count, 3)

    def test_force_and_initial_refresh_still_respect_night_audit_gate(self):
        self.run_report("jy03")
        self.run_report("jy03", "--force")
        self.run_report("jy03", "--initial")
        self.assertEqual(self.collect.call_count, 3)
        self.clock.now.return_value = datetime(2026, 10, 20, 2)
        self.run_report("jy03", "--force")
        self.assertEqual(self.collect.call_count, 3)
        self.clock.now.return_value = datetime(2026, 10, 20, 4)
        self.run_report("jy03")
        self.assertEqual(self.collect.call_count, 4)

    def test_scope_changes_require_fresh_collection(self):
        self.run_report()
        self.config.hotel_id = "OTHER"
        self.run_report()
        self.config.database = "foxhis2"
        self.run_report()
        self.config.mysql["database"] = "new_target"
        self.run_report()
        self.endpoint.return_value = ("new_source", 5000)
        self.run_report()
        self.assertEqual(self.collect.call_count, 5)

    def test_corrupt_marker_is_ignored(self):
        for value in ("broken", "[]", '{"jy01": "invalid"}'):
            self.state_path.write_text(value)
            self.run_report()
        self.assertEqual(self.collect.call_count, 3)

    def test_realtime_orders_and_checkout_ledger_are_not_daily_limited(self):
        self.clock.now.return_value = datetime(2026, 10, 19, 2)
        self.run_report("room_status", realtime=True)
        self.run_report("room_status", realtime=True)
        self.assertEqual(self.collect.call_count, 2)
        self.clock.now.return_value = datetime(2026, 10, 19, 4)
        for key in ("room_revenue", "jd01"):
            self.run_report(key)
            self.run_report(key)
        self.assertEqual(self.collect.call_count, 6)
        self.assertFalse(self.state_path.exists())


if __name__ == "__main__":
    unittest.main()
