"""西软名称留空时读取源库、回填配置，并保护人工名称与连接范围。"""
import copy
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "OTA采集服务/foxhis_pms"), str(ROOT / "OTA采集服务")]
import foxhis_common as common
import runner


class HotelNameTests(unittest.TestCase):
    def test_configured_name_does_not_query_source(self):
        with patch.object(common, "query") as query:
            self.assertEqual(common.resolve_hotel_name(common.Config(hotel_name="人工名称")), "人工名称")
        query.assert_not_called()

    def test_blank_name_reads_single_native_name(self):
        config = common.Config(hotel_name="  ")
        with patch.object(common, "query", return_value=[{"hotel_name": " 源酒店 "}]):
            self.assertEqual(common.resolve_hotel_name(config), "源酒店")
        self.assertEqual(config.hotel_name, "源酒店")

    def test_empty_ambiguous_and_failed_queries_do_not_supply_a_name(self):
        for rows in ([], [{"hotel_name": " "}], [{"hotel_name": None}], [{"hotel_name": "A"}, {"hotel_name": "B"}]):
            with self.subTest(rows=rows), patch.object(common, "query", return_value=rows):
                with self.assertRaises(common.FoxhisError):
                    common.resolve_hotel_name(common.Config())
        with patch.object(common, "query", side_effect=common.FoxhisError("查询失败")):
            with self.assertRaises(common.FoxhisError):
                common.resolve_hotel_name(common.Config())

    def test_environment_does_not_use_other_pms_name(self):
        self.assertEqual(common.Config.from_env({"PMS_HOTEL_NAME": "别样红名称"}).hotel_name, "")

    def test_standalone_report_resolves_name_before_collecting(self):
        config = common.Config(server="test", database="test", username="reader")
        collect = Mock(return_value={"row_count": 0, "written": 0})
        with patch.object(common.Config, "from_env", return_value=config), patch.object(common, "query", return_value=[{"hotel_name": "源酒店"}]), patch.object(common, "check_reachable", return_value=(True, "")), redirect_stdout(StringIO()):
            self.assertEqual(common.report_main("room_status", collect, realtime=True), 0)
        self.assertEqual(collect.call_args.args[0].hotel_name, "源酒店")


class ServiceNameBackfillTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir=ROOT)))
        self.path = directory / "settings.json"
        settings = json.loads((ROOT / "OTA采集服务/config/settings.example.json").read_text(encoding="utf-8-sig"))
        settings["pms"]["provider"] = "foxhis"
        settings["foxhis"].update(enabled=True, hotel_name="", server="FoxFront", database="foxhis1", username="reader")
        runner.save_json(self.path, settings)
        self.stack.enter_context(patch.object(runner, "CONFIG_PATH", self.path))
        self.stack.enter_context(patch.object(runner, "STATUS_PATH", directory / "status.json"))
        self.stack.enter_context(patch.object(runner, "LOG_DIR", directory / "logs"))
        self.settings = runner.load_settings()
        self.before = copy.deepcopy(self.settings)
        self.query = self.stack.enter_context(patch.object(common, "query", return_value=[{"hotel_name": "源酒店"}]))

    def test_fill_once_persists_and_all_report_processes_receive_name(self):
        completed = SimpleNamespace(return_code=0, output_tail="")
        with patch.object(runner, "run_streamed", return_value=completed) as run, patch.object(runner, "enrich_room_type_ids"), patch.dict(os.environ, {"FOXHIS_HOTEL_NAME": "旧环境名称", "PMS_HOTEL_NAME": "其他PMS名称"}):
            result = runner.run_task("pms_fetch", self.settings, {})
        self.assertEqual(result["status"], "success")
        self.assertEqual(run.call_count, 7)
        self.assertTrue(all(call.kwargs["env"]["FOXHIS_HOTEL_NAME"] == "源酒店" for call in run.call_args_list))
        self.assertEqual(self.query.call_count, 1)
        expected = copy.deepcopy(self.before)
        expected["foxhis"]["hotel_name"] = "源酒店"
        self.assertEqual(runner.load_json(self.path, {}), expected)
        runner.ensure_foxhis_hotel_name(self.settings)
        self.assertEqual(self.query.call_count, 1)

    def test_manual_value_entered_during_query_wins(self):
        def query(*args, **kwargs):
            latest = runner.load_json(self.path, {})
            latest["foxhis"]["hotel_name"] = "人工名称"
            runner.save_json(self.path, latest)
            return [{"hotel_name": "源酒店"}]
        self.query.side_effect = query
        runner.ensure_foxhis_hotel_name(self.settings)
        self.assertEqual(self.settings["foxhis"]["hotel_name"], "人工名称")
        self.assertEqual(runner.load_json(self.path, {})["foxhis"]["hotel_name"], "人工名称")

    def test_changed_source_is_not_backfilled(self):
        def query(*args, **kwargs):
            latest = runner.load_json(self.path, {})
            latest["foxhis"]["database"] = "another_source"
            runner.save_json(self.path, latest)
            return [{"hotel_name": "源酒店"}]
        self.query.side_effect = query
        with self.assertRaisesRegex(RuntimeError, "连接配置发生变化"):
            runner.ensure_foxhis_hotel_name(self.settings)
        self.assertEqual(runner.load_json(self.path, {})["foxhis"]["hotel_name"], "")

    def test_missing_source_name_fails_before_any_report_write(self):
        self.query.return_value = []
        with patch.object(runner, "run_streamed") as run:
            result = runner.run_task("pms_fetch", self.settings, {})
        self.assertEqual(result["status"], "failed")
        run.assert_not_called()
        self.assertEqual(runner.load_json(self.path, {}), self.before)


if __name__ == "__main__":
    unittest.main()
