from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CTRIP_DIR = ROOT / "携程OTA数据采集代码"


def load_module():
    sys.path.insert(0, str(CTRIP_DIR))
    path = CTRIP_DIR / "ctrip_business_data.py"
    spec = importlib.util.spec_from_file_location("ctrip_business_hourly_test_target", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CtripBusinessHourlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()
        cls.module.HOTEL_ID = "test-hotel"

    def test_build_hourly_rows_keeps_realtime_metrics_and_peer_data(self):
        captured_at = datetime(2026, 9, 11, 14, 37, 52)
        payload = {
            "management_data_realtime": {
                "data": {
                    "dataList": [
                        {"indexType": 0, "val": "3", "lastVal": "2", "rankComp": "4/20", "avgComp": "2.5"},
                        {"indexType": 3, "val": "0.3077", "lastVal": "0.2", "rankComp": "5/20", "avgComp": "0.25"},
                    ]
                }
            }
        }

        rows = self.module.build_hourly_metric_rows(payload, captured_at, "测试酒店")
        values = {row[6]: dict(zip(self.module.HOURLY_HEADERS, row)) for row in rows}

        self.assertEqual(len(rows), 2)
        self.assertEqual(values["realtime_booking_order_count"]["hotel_id"], "test-hotel")
        self.assertEqual(values["realtime_booking_order_count"]["business_date"].isoformat(), "2026-09-11")
        self.assertEqual(values["realtime_booking_order_count"]["snapshot_hour"], datetime(2026, 9, 11, 14))
        self.assertEqual(values["realtime_booking_order_count"]["competitor_rank"], "4/20")
        self.assertEqual(values["realtime_booking_order_count"]["peer_average"], 2.5)
        self.assertEqual(values["realtime_occupancy_rate"]["metric_value"], 30.77)
        self.assertEqual(values["realtime_occupancy_rate"]["peer_average"], 25)

    def test_optional_hotel_title_failure_does_not_block_business_requests(self):
        client = self.module.CtripBusinessClient("")
        requests = []

        def fake_post(path, payload, content_type=None):
            requests.append(path)
            if path == self.module.ENDPOINTS["visitor_title"]:
                raise self.module.CtripApiError("HTML response")
            return {"data": {"dataList": []}}

        client.post_json = fake_post
        payload = client.query_all()

        self.assertEqual(payload["visitor_title"], {})
        self.assertIn(self.module.ENDPOINTS["management_data"], requests)
        self.assertIn(self.module.ENDPOINTS["flow_data"], requests)


if __name__ == "__main__":
    unittest.main()
