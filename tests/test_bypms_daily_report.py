from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "宝寓PMS数据采集代码" / "bypms_daily_report_data.py"


def load_target():
    os.environ.setdefault("HOTEL_ID", "test-hotel")
    spec = importlib.util.spec_from_file_location("test_bypms_daily_report_target", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BypmsDailyReportTests(unittest.TestCase):
    def test_unassigned_room_type_is_excluded_from_jl01(self):
        target = load_target()
        payload = {"data": {"today": "2026-09-22", "roomType": [
            {"roomTypeName": "未指定房型", "roomTypeNightCount": 3},
            {"roomTypeName": "标准大床房", "roomTypeNightCount": 2},
        ]}}
        rows = target.build_rows(payload, date(2026, 9, 22), datetime(2026, 9, 23, 6))
        self.assertEqual([row["room_type_name"] for row in rows], ["标准大床房"])

    def test_room_type_metrics_map_to_jl01_contract(self):
        target = load_target()
        rows = target.build_rows(
            {"state": 0, "data": {"today": "2026-09-21", "companyName": "测试酒店", "roomType": [{
                "roomTypeName": "标准大床房", "roomTypeNightCount": 4, "roomTypeOccupancyRate": 0.8,
                "roomTypeContractIncomePrice": 301.36, "roomTypeAvgContractIncomePrice": 75.34,
                "roomTypeRevPar": 60.27,
            }]}},
            date(2026, 9, 22),
            datetime(2026, 9, 22, 6, 21),
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["business_date"], date(2026, 9, 21))
        self.assertEqual(rows[0]["room_nights"], 4)
        self.assertEqual(rows[0]["occupancy_rate"], 80)
        self.assertEqual(rows[0]["room_revenue"], 301.36)
        self.assertEqual(rows[0]["adr"], 75.34)
        self.assertEqual(rows[0]["revpar"], 60.27)
        self.assertEqual(rows[0]["source_platform"], "PMS（宝寓）")

        total = target.build_jy01_rows(
            {"data": {"today": "2026-09-21", "companyName": "测试酒店", "roomCount": 52, "nightCount": 27,
                      "contractIncomeTotalPrice": 1933.92, "occupancyRate": 0.5192,
                      "contractIncomeAvgPrice": 71.63, "revPar": 37.19, "overNightRoomCount": 27,
                      "emptyRoomCount": 25, "newContractCount": 31}},
            date(2026, 9, 22), datetime(2026, 9, 22, 6, 21),
        )[0]
        self.assertEqual(total["dimension_type"], "总营业指标")
        self.assertEqual(total["room_count"], 52)
        self.assertEqual(total["room_nights"], 27)
        self.assertEqual(total["room_revenue"], 1933.92)
        self.assertEqual(total["occupancy_rate"], 51.92)
        self.assertEqual(total["orders_today"], 31)

    def test_collection_dates_cover_recent_30_closed_days(self):
        target = load_target()
        dates = target.collection_dates(date(2026, 9, 22))

        self.assertEqual(len(dates), 30)
        self.assertEqual(dates[0], date(2026, 8, 23))
        self.assertEqual(dates[-1], date(2026, 9, 21))

    def test_jl01_only_collects_yesterday_and_last_year_same_day(self):
        target = load_target()
        self.assertEqual(
            target.jl01_collection_dates(date(2026, 9, 22)),
            [date(2026, 9, 21), date(2025, 9, 21)],
        )

    def test_sync_uses_jl01_table_and_upsert(self):
        target = load_target()

        class Cursor:
            def __init__(self):
                self.sql = ""

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def executemany(self, sql, _rows):
                self.sql = sql

        class Connection:
            def __init__(self):
                self.cursor_instance = Cursor()

            def cursor(self):
                return self.cursor_instance

            def commit(self):
                pass

            def rollback(self):
                pass

        connection = Connection()
        target.sync_mysql([{"hotel_id": "test-hotel"}], connection)
        self.assertIn("jl01_room_type_performance_daily", connection.cursor_instance.sql)
        self.assertIn("ON DUPLICATE KEY UPDATE", connection.cursor_instance.sql)

        target.sync_jy01([{"hotel_id": "test-hotel"}], connection)
        self.assertIn("jy01_hotel_statistics_daily", connection.cursor_instance.sql)


if __name__ == "__main__":
    unittest.main()
