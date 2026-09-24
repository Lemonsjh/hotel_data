from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from datetime import date, datetime
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "宝寓PMS数据采集代码" / "bypms_jl11_data.py"


def load_target():
    os.environ.setdefault("HOTEL_ID", "test-hotel")
    spec = importlib.util.spec_from_file_location("test_bypms_jl11_target", SCRIPT)
    assert spec and spec.loader
    target = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = target
    spec.loader.exec_module(target)
    return target


class BypmsJl11Tests(unittest.TestCase):
    def test_summary_fields_and_window(self):
        target = load_target()
        start, end = target.report_window(date(2026, 9, 23))
        self.assertEqual((start, end), (date(2026, 8, 24), date(2026, 9, 22)))
        rows = target.build_rows([{
            "id": 217401, "title": "甄选电竞双人大床", "roomCount": 168,
            "roomCountSold": 111, "roomSoldPercent": 0.66,
            "price": 12722.24, "priceAvg": 114.61, "revPar": 75.73,
        }], start, end, datetime(2026, 9, 23, 10))
        self.assertEqual(rows[0]["section"], "summary")
        self.assertEqual(rows[0]["room_count"], 168)
        self.assertEqual(rows[0]["room_nights"], 111)
        self.assertEqual(rows[0]["occupancy_rate"], 66)
        self.assertEqual(rows[0]["overnight_room_count"], 111)
        self.assertEqual(rows[0]["overnight_occupancy_rate"], 66)
        self.assertEqual(rows[0]["room_revenue"], 12722.24)
        self.assertEqual(rows[0]["average_room_price"], 114.61)
        self.assertEqual(rows[0]["revpar"], 75.73)

    def test_incomplete_report_is_rejected_before_database_replacement(self):
        target = load_target()
        with self.assertRaises(RuntimeError):
            target.build_rows([{"title": "甄选电竞双人大床", "roomCount": 168}],
                              date(2026, 8, 24), date(2026, 9, 22), datetime(2026, 9, 23))


if __name__ == "__main__":
    unittest.main()
