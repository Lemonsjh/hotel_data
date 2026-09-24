from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from datetime import date, datetime
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "宝寓PMS数据采集代码" / "bypms_jy03_data.py"


def load_target():
    os.environ.setdefault("HOTEL_ID", "test-hotel")
    spec = importlib.util.spec_from_file_location("test_bypms_jy03_target", SCRIPT)
    assert spec and spec.loader
    target = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = target
    spec.loader.exec_module(target)
    return target


class BypmsJy03Tests(unittest.TestCase):
    def test_initial_months_include_three_current_and_three_last_year(self):
        target = load_target()
        months = target.collection_months(date(2026, 9, 23), True)
        self.assertEqual([value.strftime("%Y-%m") for value in months], [
            "2026-09", "2026-08", "2026-07", "2025-09", "2025-08", "2025-07",
        ])
        self.assertEqual(target.month_end(months[0], date(2026, 9, 23)), date(2026, 9, 22))
        self.assertEqual(target.month_end(months[3], date(2026, 9, 23)), date(2025, 9, 22))
        self.assertEqual(target.collection_months(date(2026, 9, 23), False), [date(2026, 9, 1)])

    def test_monthly_total_and_dimensions_preserve_metric_meaning(self):
        target = load_target()
        rows = target.build_rows({
            "companyName": "测试酒店", "roomCount": 1134, "nightCount": 731,
            "contractIncomeTotalPrice": 56749.47, "fixRoomCount": 0,
            "occupancyRate": 0.6446, "contractIncomeAvgPrice": 77.63, "revPar": 50.04,
            "roomType": [{"roomTypeName": "大床房", "roomTypeRoomCount": 60,
                          "roomTypeNightCount": 30, "roomTypeContractIncomePrice": 3000,
                          "roomTypeOccupancyRate": 0.5, "roomTypeAvgContractIncomePrice": 100,
                          "roomTypeRevPar": 50}],
            "channel": [{"channelName": "美团酒店", "channelNightCount": 100,
                         "channelContractIncomePrice": 8000, "channelAvgContractIncomePrice": 80,
                         "channelPercent": 0.2}],
        }, date(2026, 9, 1), datetime(2026, 9, 23, 10))
        self.assertEqual((rows[0]["dimension_type"], rows[0]["dimension_name"]), ("总营业指标", "总营业指标"))
        self.assertEqual(rows[0]["room_count"], 1134)
        self.assertEqual(rows[0]["occupancy_rate"], 64.46)
        self.assertEqual(rows[1]["dimension_type"], "房型")
        self.assertEqual(rows[1]["room_count"], 60)
        self.assertEqual(rows[2]["dimension_name"], "美团EBK")
        self.assertIsNone(rows[2]["occupancy_rate"])
        self.assertEqual(rows[2]["room_revenue"], 8000)

    def test_no_data_month_is_skipped_but_wrong_dates_fail(self):
        target = load_target()

        class Session:
            def __init__(self, payload):
                self.payload = payload

            def get(self, *_args, **_kwargs):
                return self

            def raise_for_status(self):
                pass

            def json(self):
                return self.payload

        start, end = date(2025, 9, 1), date(2025, 9, 22)
        self.assertIsNone(target.fetch_month(start, end, Session({"state": 0, "data": {}})))
        with self.assertRaises(RuntimeError):
            target.fetch_month(start, end, Session({"state": 0, "data": {
                "statisticsStartDate": "2025-08-01", "statisticsEndDate": "2025-08-31",
            }}))


if __name__ == "__main__":
    unittest.main()
