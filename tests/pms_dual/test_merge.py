import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "OTA采集服务")]
import pms_providers
from rezen_pms.storage import sync_mysql

from pms_dual.merge import aliases_from_rows, merge_rows
from pms_dual.room_status import run
from pms_dual.state import remember, remember_room_types


STAMP = datetime(2026, 11, 10, 10, 0, 0)


def row(name, raw_id, occupied, revenue, total=10, available=4, day=date(2026, 11, 10), over=0):
    return {
        "hotel_id": "h1", "hotel_name": "测试酒店", "stay_date": day,
        "room_type_name": name, "pms_room_type_id": raw_id,
        "total_rooms": total, "available_rooms": available, "occupied_rooms": occupied,
        "overbooking_rooms": over, "room_revenue": None if revenue is None else Decimal(revenue),
        "adr": None, "revpar": None, "snapshot_time": STAMP, "snapshot_hour": STAMP,
    }


class MergeTests(unittest.TestCase):
    def test_same_unified_room_type_is_summed_and_rates_recalculated(self):
        aliases = {("pms_meituan", "溪眠"): "U1", ("pms_rezen", "溪眠舒适"): "U1"}
        rows = merge_rows(
            [row("溪眠", "11", 2, "100", total=20, available=8, over=1)],
            [row("溪眠舒适", "SSSC", 1, "50", total=10, available=4, over=2)],
            aliases, hotel_id="h1", hotel_name="测试酒店", snapshot=STAMP,
            left_platform="pms_meituan", right_platform="pms_rezen")
        self.assertEqual(len(rows), 1)
        merged = rows[0]
        self.assertEqual(merged["pms_room_type_id"], "U1")
        self.assertEqual(merged["room_type_id"], "U1")
        self.assertEqual(merged["occupied_rooms"], 3)
        self.assertEqual(merged["total_rooms"], 30)
        self.assertEqual(merged["available_rooms"], 12)
        self.assertEqual(merged["overbooking_rooms"], 3)
        self.assertEqual(merged["room_revenue"], Decimal("150.0000"))
        self.assertEqual(merged["adr"], Decimal("50.0000"))
        self.assertEqual(merged["revpar"], Decimal("5.0000"))
        self.assertEqual(merged["source_platform"], "PMS（美团+Rezen）")

    def test_a_missing_metric_stays_empty_instead_of_becoming_zero(self):
        rows = merge_rows(
            [row("溪眠", "11", 2, None, total=20)],
            [row("溪眠舒适", "SSSC", 1, "50", total=10)],
            {("pms_meituan", "溪眠"): "U1", ("pms_rezen", "溪眠舒适"): "U1"},
            hotel_id="h1", hotel_name="测试酒店", snapshot=STAMP,
            left_platform="pms_meituan", right_platform="pms_rezen")
        self.assertEqual(rows[0]["total_rooms"], 30)
        self.assertIsNone(rows[0]["room_revenue"])
        self.assertIsNone(rows[0]["adr"])

    def test_same_name_is_summed_like_one_hotel(self):
        separate = merge_rows(
            [row("溪眠", "11", 2, "100")], [row("筑风", "FSSC", 1, "50")], {},
            hotel_id="h1", hotel_name="测试酒店", snapshot=STAMP,
            left_platform="pms_meituan", right_platform="pms_rezen")
        self.assertEqual({row["pms_room_type_id"] for row in separate}, {"mt:11", "rz:FSSC"})
        same_name = merge_rows(
            [row("溪眠", "11", 2, "100", total=20)], [row("溪眠", "SSSC", 1, "50", total=10)], {},
            hotel_id="h1", hotel_name="测试酒店", snapshot=STAMP,
            left_platform="pms_meituan", right_platform="pms_rezen")
        self.assertEqual(len(same_name), 1)
        self.assertEqual(same_name[0]["room_type_name"], "溪眠")
        self.assertEqual(same_name[0]["pms_room_type_id"], "溪眠")
        self.assertIsNone(same_name[0]["room_type_id"])
        self.assertEqual(same_name[0]["occupied_rooms"], 3)
        self.assertEqual(same_name[0]["total_rooms"], 30)
        self.assertEqual(same_name[0]["adr"], Decimal("50.0000"))
        one_side = merge_rows(
            [row("溪眠", "11", 2, "100", total=20)], [row("溪眠", "SSSC", 1, "50", total=10)],
            {("pms_meituan", "溪眠"): "U1"},
            hotel_id="h1", hotel_name="测试酒店", snapshot=STAMP,
            left_platform="pms_meituan", right_platform="pms_rezen")
        self.assertEqual(len(one_side), 1)
        self.assertEqual(one_side[0]["pms_room_type_id"], "U1")
        self.assertEqual(one_side[0]["occupied_rooms"], 3)
        in_house = row("溪眠", "SSSC", 3, "50", total=10)
        in_house["in_house_rooms"] = 1
        rated = merge_rows(
            [row("溪眠", "11", 2, "100", total=20)], [in_house], {},
            hotel_id="h1", hotel_name="测试酒店", snapshot=STAMP,
            left_platform="pms_meituan", right_platform="pms_rezen")
        self.assertEqual(rated[0]["occupied_rooms"], 5)
        self.assertEqual(rated[0]["adr"], Decimal("50.0000"))
        with self.assertRaisesRegex(ValueError, "不同的统一房型"):
            merge_rows([row("溪眠", "11", 2, "100")], [row("溪眠", "SSSC", 1, "50")],
                       {("pms_meituan", "溪眠"): "U1", ("pms_rezen", "溪眠"): "U2"},
                       hotel_id="h1", hotel_name="测试酒店", snapshot=STAMP,
                       left_platform="pms_meituan", right_platform="pms_rezen")

    def test_future_day_present_on_only_one_side_is_left_out(self):
        rows = merge_rows(
            [row("溪眠", "11", 2, "100")],
            [row("筑风", "FSSC", 1, "50"), row("筑风", "FSSC", 3, "80", day=date(2026, 11, 11))],
            {}, hotel_id="h1", hotel_name="测试酒店", snapshot=STAMP,
            left_platform="pms_meituan", right_platform="pms_rezen")
        self.assertEqual({row["stay_date"] for row in rows}, {date(2026, 11, 10)})

    def test_stored_id_collision_rejects_the_batch(self):
        aliases = {("pms_rezen", "筑风"): "mt:11"}
        with self.assertRaisesRegex(ValueError, "主键冲突"):
            merge_rows([row("溪眠", "11", 2, "100")], [row("筑风", "FSSC", 1, "50")], aliases,
                       hotel_id="h1", hotel_name="测试酒店", snapshot=STAMP,
                       left_platform="pms_meituan", right_platform="pms_rezen")

    def test_conflicting_alias_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "多个统一房型"):
            aliases_from_rows([("pms_rezen", "溪眠", "U1"), ("pms_rezen", "溪眠", "U2")])

    def test_one_side_failing_records_only_the_success_and_does_not_write(self):
        meituan = [row("溪眠", "11", 2, "100")]
        with tempfile.TemporaryDirectory() as folder, \
             patch("pms_dual.room_status.collect_meituan", return_value=meituan), \
             patch("pms_dual.room_status.collect_rezen", side_effect=RuntimeError("Rezen 请求失败")), \
             patch("pms_dual.room_status.sync_mysql") as sync, \
             patch.dict(os.environ, {"HOTEL_ID": "h1", "MEITUAN_PMS_HOTEL_NAME": "测试酒店",
                                     "REZEN_HOTEL_NAME": "测试酒店"}, clear=False):
            state = Path(folder) / "pms_dual.json"
            with patch("pms_dual.room_status.remember", side_effect=lambda side: remember(side, path=state)):
                with self.assertRaisesRegex(RuntimeError, "Rezen"):
                    run(aliases={})
            sync.assert_not_called()
            text = state.read_text(encoding="utf-8")
        self.assertIn("meituan", text)
        self.assertNotIn("rezen", text)
        self.assertNotIn("溪眠", text)

    def test_successful_collection_remembers_each_sides_original_names(self):
        meituan = [row("标准大床房", "11", 2, "100")]
        rezen = [row("雅致大床房", "FSSC", 1, "50")]
        with tempfile.TemporaryDirectory() as folder, \
             patch("pms_dual.room_status.collect_meituan", return_value=meituan), \
             patch("pms_dual.room_status.collect_rezen", return_value=rezen), \
             patch.dict(os.environ, {"HOTEL_ID": "h1", "MEITUAN_PMS_HOTEL_NAME": "测试酒店",
                                     "REZEN_HOTEL_NAME": "测试酒店"}, clear=False):
            state = Path(folder) / "pms_dual.json"
            with patch("pms_dual.room_status.remember", side_effect=lambda side: remember(side, path=state)), \
                 patch("pms_dual.room_status.remember_room_types",
                       side_effect=lambda left, right: remember_room_types(left, right, path=state)):
                run(aliases={})
            data = json.loads(state.read_text(encoding="utf-8"))
        self.assertEqual(data["room_types"]["meituan"], ["标准大床房"])
        self.assertEqual(data["room_types"]["rezen"], ["雅致大床房"])
        self.assertNotIn(" / ", json.dumps(data["room_types"], ensure_ascii=False))

    def test_removing_one_room_type_changes_the_written_range(self):
        aliases = {("pms_meituan", "溪眠"): "U1", ("pms_rezen", "溪眠舒适"): "U1"}
        both = merge_rows([row("溪眠", "11", 2, "100", total=20)],
                          [row("溪眠舒适", "SSSC", 1, "50", total=10)], aliases,
                          hotel_id="h1", hotel_name="测试酒店", snapshot=STAMP,
                          left_platform="pms_meituan", right_platform="pms_rezen")
        without_rezen_type = merge_rows([row("溪眠", "11", 2, "100", total=20)],
                                         [row("筑风", "FSSC", 4, "80", total=8)], {},
                                         hotel_id="h1", hotel_name="测试酒店", snapshot=STAMP,
                                         left_platform="pms_meituan", right_platform="pms_rezen")
        self.assertEqual(both[0]["occupied_rooms"], 3)
        self.assertEqual(both[0]["total_rooms"], 30)
        self.assertEqual(both[0]["revpar"], Decimal("5.0000"))
        self.assertEqual({row["pms_room_type_id"] for row in without_rezen_type}, {"mt:11", "rz:FSSC"})
        self.assertNotIn("U1", {row["pms_room_type_id"] for row in without_rezen_type})

    def test_dual_run_writes_both_tables_from_the_same_merged_rows(self):
        meituan = [row("溪眠", "11", 2, "100", total=20)]
        rezen = [row("溪眠舒适", "SSSC", 1, "50", total=10)]
        aliases = {("pms_meituan", "溪眠"): "U1", ("pms_rezen", "溪眠舒适"): "U1"}
        with patch("pms_dual.room_status.collect_meituan", return_value=meituan), \
             patch("pms_dual.room_status.collect_rezen", return_value=rezen), \
             patch("pms_dual.room_status.remember"), \
             patch.dict(os.environ, {"HOTEL_ID": "h1", "MEITUAN_PMS_HOTEL_NAME": "测试酒店",
                                     "REZEN_HOTEL_NAME": "测试酒店"}, clear=False):
            result = run(aliases=aliases)
        self.assertEqual(result["forecast"], result["hourly"])
        self.assertEqual(result["forecast"][0]["total_rooms"], 30)
        self.assertEqual(result["forecast"][0]["revpar"], Decimal("5.0000"))

    def test_dual_provider_keeps_both_logins_and_the_same_report_columns(self):
        settings = {"pms": {}, "meituan_pms": {"enabled": False}, "huiyun": {"enabled": True},
                    "rezen": {"enabled": False}}
        self.assertTrue(pms_providers.apply_provider_selection(settings, "meituan_rezen"))
        self.assertEqual(pms_providers.enabled_provider_keys("meituan_rezen"),
                         {"meituan_rezen", "meituan_pms", "rezen"})
        self.assertTrue(settings["meituan_pms"]["enabled"])
        self.assertTrue(settings["rezen"]["enabled"])
        self.assertFalse(settings["huiyun"]["enabled"])
        self.assertEqual(pms_providers.task_specs("pms_fetch", settings), [
            ("meituan_rezen", "room_status.py", []),
            ("meituan_rezen", "daily_report.py", []),
            ("meituan_rezen", "monthly_report.py", []),
            ("meituan_rezen", "performance_report.py", []),
            ("meituan_rezen", "room_performance_report.py", []),
            ("meituan_rezen", "classification_report.py", []),
            ("meituan_rezen", "revenue_report.py", []),
            ("meituan_rezen", "booking_report.py", []),
            ("meituan_rezen", "guest_report.py", []),
        ])
        for _provider, script, _args in pms_providers.task_specs("pms_fetch", settings):
            self.assertTrue(pms_providers.script_path(settings, "meituan_rezen", script).is_file())

    def test_combined_source_replaces_hotel_forecast_and_only_its_own_hourly_rows(self):
        forecast = [row("溪眠 / 溪眠舒适", "U1", 3, "150")]
        hourly = [row("溪眠 / 溪眠舒适", "U1", 3, None)]
        for item in (*forecast, *hourly):
            item["source_platform"] = "PMS（美团+Rezen）"
            item["room_type_id"] = "U1"
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = None
        with patch.dict(os.environ, {"PMS_RETENTION": "{}"}):
            sync_mysql(forecast, hourly, source="PMS（美团+Rezen）", connection=connection)
        connection.commit.assert_called_once()
        deletes = [call.args for call in cursor.execute.call_args_list if "DELETE FROM" in call.args[0]]
        self.assertEqual(deletes[0], ("DELETE FROM `pms_room_type_forecast` WHERE hotel_id=%s", ("h1",)))
        self.assertTrue(all(args[:2] == ("h1", "PMS（美团+Rezen）") for _, args in deletes[1:]))
