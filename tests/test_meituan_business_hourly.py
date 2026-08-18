from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MEITUAN_DIR = ROOT / "美团OTA数据采集代码"


def load_module():
    sys.path.insert(0, str(MEITUAN_DIR))
    path = MEITUAN_DIR / "bussiness_data.py"
    spec = importlib.util.spec_from_file_location("meituan_business_hourly_test_target", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class MeituanBusinessHourlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()
        cls.module.HOTEL_ID = "test-hotel"

    def test_build_hourly_snapshot_keeps_only_ten_realtime_metrics(self):
        metrics = {
            "DAY_ROOM_LOWEST_PRICE_AVG": {"value": "197.00", "unit": "元"},
            "EXPOSE_PV_CNT": {"value": "-", "unit": "次"},
            "INTENTION_UV": {"value": "65", "unit": "人"},
            "PAY_ORDER_CNT_UV": {"value": "1.54", "unit": "%"},
            "PAY_ORDER_CNT": {"value": "1", "unit": "单"},
            "PAY_ROOMNIGHT": {"value": "1", "unit": "间夜"},
            "PAY_ADR": {"value": "88.00", "unit": "元"},
            "PAY_AMT": {"value": "88.00", "unit": "元"},
            "CONSUME_ROOMNIGHT_SPLIT_EX_7DAYS_REFUND": {"value": "2", "unit": "间夜"},
            "NOT_AVAILABLE_REAL_ROOM_RATE": {"value": "0.00", "unit": "%"},
            "FLOW_EXPOSURE_UV": {"value": "999", "unit": "人"},
        }
        captured_at = datetime(2026, 8, 18, 10, 56, 32)

        row = self.module.build_hourly_snapshot({"score_hotel_name": "测试酒店", "metrics": metrics}, captured_at)
        values = dict(zip(self.module.HOURLY_HEADERS, row))

        self.assertEqual(values["hotel_id"], "test-hotel")
        self.assertEqual(values["business_date"].isoformat(), "2026-08-18")
        self.assertEqual(values["snapshot_hour"], datetime(2026, 8, 18, 10))
        self.assertEqual(values["traffic_price"], 197)
        self.assertIsNone(values["exposure_count"])
        self.assertEqual(values["payment_conversion_rate"], 0.0154)
        self.assertEqual(values["occupancy_rate"], 0)
        self.assertEqual(values["sales_amount"], 88)
        self.assertNotIn("FLOW_EXPOSURE_UV", values)


if __name__ == "__main__":
    unittest.main()
