from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "宝寓PMS数据采集代码"
SCRIPT_PATH = SCRIPT_DIR / "bypms_rs01_data.py"


def load_target():
    os.environ.setdefault("HOTEL_ID", "test-hotel")
    sys.path.insert(0, str(SCRIPT_DIR))
    spec = importlib.util.spec_from_file_location("test_bypms_rs01_target", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BypmsRs01Tests(unittest.TestCase):
    def test_current_month_window(self):
        target = load_target()
        self.assertEqual(target.current_month_window(date(2026, 9, 22)), (date(2026, 9, 1), date(2026, 9, 30)))

    def test_prefers_channel_mapping_then_falls_back_to_room_master(self):
        target = load_target()
        payments = [
            {
                "flowType": "I", "paymentType": "收房费", "occurTime": "2026-09-17 00:49:03",
                "id": 1001, "roomId": 1, "channel": "Meituan", "channelUnitId": "goods-1", "contractId": 10,
                "amount": 1, "priceFang": 66.42, "priceNight": 92, "paymentChannel": "平台代收",
            },
            {
                "flowType": "I", "paymentType": "收房费", "occurTime": "2026-09-17 01:00:00",
                "roomId": 2, "channel": "Meituan", "channelUnitId": "ambiguous", "contractId": 11,
            },
        ]
        rows = target.build_rows(
            payments,
            {("meituan", "goods-1"): {"渠道映射房型"}, ("meituan", "ambiguous"): {"房型A", "房型B"}},
            {"1": "302", "2": "303"},
            {"1": "房态兜底房型1", "2": "房态兜底房型2"},
            datetime(2026, 9, 22, 10, 0),
        )

        self.assertEqual(rows[0]["room_no"], "302")
        self.assertEqual(rows[0]["room_type_name"], "渠道映射房型")
        self.assertEqual(rows[0]["room_fee"], 66.42)
        self.assertEqual(rows[0]["business_date"], date(2026, 9, 17))
        self.assertEqual(rows[0]["order_id"], "10:1001")
        self.assertEqual(rows[0]["channel_unit_id"], "goods-1")
        self.assertEqual(rows[1]["room_type_name"], "房态兜底房型2")

    def test_sync_writes_existing_rs01_table(self):
        target = load_target()

        class Cursor:
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
        self.assertIn("rs01_room_revenue_daily", connection.cursor_instance.sql)
        self.assertIn("channel_unit_id", connection.cursor_instance.sql)
        self.assertIn("ON DUPLICATE KEY UPDATE", connection.cursor_instance.sql)


if __name__ == "__main__":
    unittest.main()
