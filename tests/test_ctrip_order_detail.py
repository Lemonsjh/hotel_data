from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = ROOT / "OTA采集服务"
CTRIP_DIR = ROOT / "携程OTA数据采集代码"


def load_module():
    sys.path.insert(0, str(SERVICE_DIR))
    sys.path.insert(0, str(CTRIP_DIR))
    spec = importlib.util.spec_from_file_location(
        "ctrip_order_detail_test_target", CTRIP_DIR / "ctrip_order_detail_data.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load ctrip_order_detail_data.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CtripOrderDetailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_retries_one_missing_order_list_response(self):
        class Client:
            def __init__(self):
                self.responses = [
                    {"resStatus": {"rcode": 200}},
                    {"resStatus": {"rcode": 200}, "orderList": [{"formId": "1"}]},
                ]

            def post_json(self, *_):
                return self.responses.pop(0)

        with patch.object(self.module.time, "sleep") as sleep:
            rows = self.module.fetch_orders(Client(), date(2026, 9, 1), date(2026, 9, 10), datetime(2026, 9, 10))
        self.assertEqual([row["formId"] for row in rows], ["1"])
        sleep.assert_called_once_with(1)

    def test_pagination_starts_from_zero(self):
        class Client:
            def __init__(self):
                self.page_indexes = []

            def post_json(self, _, payload):
                self.page_indexes.append(payload["orderQueryCondition"]["pageInfo"]["pageIndex"])
                return {"resStatus": {"rcode": 200}, "orderList": [{"formId": "1"}]}

        client = Client()
        self.module.fetch_orders(client, date(2026, 9, 1), date(2026, 9, 10), datetime(2026, 9, 10))
        self.assertEqual(client.page_indexes, [0])

    def test_total_stops_full_final_page_without_out_of_range_request(self):
        class Client:
            def __init__(self):
                self.page_indexes = []

            def post_json(self, _, payload):
                page_index = payload["orderQueryCondition"]["pageInfo"]["pageIndex"]
                self.page_indexes.append(page_index)
                start = page_index * 20
                return {
                    "resStatus": {"rcode": 200},
                    "total": 40,
                    "orderList": [{"formId": str(start + offset)} for offset in range(20)],
                }

        client = Client()
        rows = self.module.fetch_orders(client, date(2026, 9, 1), date(2026, 9, 10), datetime(2026, 9, 10))
        self.assertEqual(client.page_indexes, [0, 1])
        self.assertEqual(len(rows), 40)


if __name__ == "__main__":
    unittest.main()
