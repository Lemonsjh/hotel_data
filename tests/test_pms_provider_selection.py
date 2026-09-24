from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "OTA采集服务"))

import runner
from process_runner import ProcessResult


class PmsProviderSelectionTests(TestCase):
    def test_bypms_hotel_name_is_backfilled_only_when_blank(self):
        settings = {"pms": {"provider": "bypms"}, "bypms": {"hotel_name": ""}, "tasks": {}}
        with patch.object(runner, "load_settings", return_value=settings), patch.object(runner, "save_json") as save:
            self.assertTrue(runner.backfill_bypms_hotel_name("测试宝寓酒店"))
        self.assertEqual(settings["bypms"]["hotel_name"], "测试宝寓酒店")
        self.assertTrue(save.called)

        with patch.object(runner, "load_settings", return_value=settings), patch.object(runner, "save_json") as save:
            self.assertFalse(runner.backfill_bypms_hotel_name("另一家酒店"))
        self.assertFalse(save.called)

    def test_only_active_pms_provider_tasks_are_enabled(self):
        byh = {"pms": {"provider": "byh", "enabled": True}, "tasks": {"pms_fetch": True}}
        bypms = {"pms": {"provider": "bypms", "enabled": False}, "bypms": {"enabled": True}, "tasks": {"pms_fetch": True}}

        self.assertIn("pms_fetch", runner.enabled_tasks(byh))
        self.assertIn("pms_fetch", runner.enabled_tasks(bypms))
        self.assertEqual(runner.task_specs("pms_fetch", byh), [("pms", "fetch_main.py", [])])
        self.assertEqual(runner.task_specs("pms_fetch", bypms), [
            ("bypms", "bypms_room_status_data.py", []),
            ("bypms", "bypms_channel_mapping_data.py", []),
            ("bypms", "bypms_rs01_data.py", []),
            ("bypms", "bypms_daily_report_data.py", []),
            ("bypms", "bypms_jl11_data.py", []),
            ("bypms", "bypms_jy03_data.py", []),
            ("bypms", "bypms_jd01_data.py", []),
        ])

    def test_bypms_runs_both_collectors_under_pms_fetch(self):
        settings = {
            "pms": {"provider": "bypms", "enabled": False},
            "bypms": {"enabled": True},
            "tasks": {"pms_fetch": True},
            "hotel": {}, "mysql": {}, "paths": {}, "service": {},
        }
        calls: list[list[str]] = []
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runner, "LOG_DIR", Path(directory)), \
                 patch.object(runner, "save_json"), \
                 patch.object(runner, "enrich_room_type_ids"), \
                 patch.object(runner, "run_streamed", side_effect=lambda command, **_: (calls.append(command) or ProcessResult(0, "ok"))):
                result = runner.run_task("pms_fetch", settings, {})

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(calls), 7)
        self.assertTrue(calls[0][1].endswith("bypms_room_status_data.py"))
        self.assertTrue(calls[1][1].endswith("bypms_channel_mapping_data.py"))
        self.assertTrue(calls[2][1].endswith("bypms_rs01_data.py"))
        self.assertTrue(calls[3][1].endswith("bypms_daily_report_data.py"))
        self.assertTrue(calls[4][1].endswith("bypms_jl11_data.py"))
        self.assertTrue(calls[5][1].endswith("bypms_jy03_data.py"))
        self.assertTrue(calls[6][1].endswith("bypms_jd01_data.py"))
