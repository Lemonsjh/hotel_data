from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from datetime import date, datetime
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "宝寓PMS数据采集代码" / "bypms_jd01_data.py"


def target():
    os.environ.setdefault("HOTEL_ID", "test-hotel")
    spec = importlib.util.spec_from_file_location("test_bypms_jd01_target", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BypmsJd01Tests(unittest.TestCase):
    def test_contract_and_night_fields_are_not_confused_with_product(self):
        module = target()
        rows = module.transform([{
            "id": 123, "state": "P", "createTime": "2026-09-23 10:00:00",
            "checkIn": "2026-09-23", "checkOut": "2026-09-27", "amount": 4,
            "priceFang": 360, "unitName": "渠道商品名", "channel": "Meituan",
        }], [{
            "contractId": 123, "roomTypeName": "PMS房型名", "checkIn": "2026-09-23",
            "checkOut": "2026-09-27", "state": "P",
        }], date(2026, 8, 23), datetime(2026, 9, 23, 11))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["order_id"], "123")
        self.assertEqual(rows[0]["room_type_name"], "PMS房型名")
        self.assertEqual(rows[0]["room_count"], 1)
        self.assertEqual(rows[0]["room_price"], 90)
        self.assertEqual(rows[0]["booking_status"], "预订")

    def test_empty_result_and_ambiguous_room_type(self):
        module = target()
        self.assertEqual(module.transform([], [], date(2026, 8, 23), datetime.now()), [])
        row = {"id": 1, "state": "X", "checkIn": "2026-09-23", "checkOut": "2026-09-24"}
        nights = [
            {"contractId": 1, "roomTypeName": "A", "checkIn": "2026-09-23", "checkOut": "2026-09-24"},
            {"contractId": 1, "roomTypeName": "B", "checkIn": "2026-09-23", "checkOut": "2026-09-24"},
        ]
        result = module.transform([row], nights, date(2026, 8, 23), datetime.now())
        self.assertIsNone(result[0]["room_type_name"])
        self.assertEqual(result[0]["room_count"], 2)
        self.assertEqual(result[0]["booking_status"], "未知(X)")

    def test_wrong_page_fails_without_partial_snapshot(self):
        module = target()

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"state": 0, "data": {"contracts": [{"id": 1}], "nights": [],
                                              "_page": {"pageIndex": 2, "total": 1}}}

        class Session:
            def post(self, *_args, **_kwargs):
                return Response()

        with self.assertRaisesRegex(RuntimeError, "wrong pagination"):
            module.fetch_contracts(Session(), date(2026, 8, 23))

    def test_request_matches_normal_orders_page(self):
        module = target()

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"state": 0, "data": {"contracts": [{"id": 1, "state": "D"}], "nights": [],
                                              "_page": {"pageIndex": 1, "total": 1}}}

        class Session:
            def __init__(self):
                self.request = None

            def post(self, _url, *, json, **_kwargs):
                self.request = json
                return Response()

        session = Session()
        contracts, _ = module.fetch_contracts(session, date(2026, 8, 23))
        self.assertEqual(session.request, {
            "contractState": "D", "checkOut": "2026-08-23", "_pidx": 1, "_pageSize": 20,
        })
        self.assertEqual([row["state"] for row in contracts], ["D"])

    def test_zero_total_with_wrong_page_index_is_valid_empty_state(self):
        module = target()

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"state": 0, "data": {"contracts": [], "nights": [],
                                              "_page": {"pageIndex": 20, "pageSize": 1, "total": 0}}}

        class Session:
            def post(self, *_args, **_kwargs):
                return Response()

        self.assertEqual(module.fetch_contracts(Session(), date(2026, 8, 23)), ([], []))

    def test_checkout_cutoff_is_one_calendar_month_ago(self):
        module = target()
        self.assertEqual(module.checkout_cutoff(date(2026, 9, 23)), date(2026, 8, 23))
        self.assertEqual(module.checkout_cutoff(date(2026, 3, 31)), date(2026, 2, 28))
        self.assertEqual(module.checkout_cutoff(date(2026, 1, 31)), date(2025, 12, 31))


if __name__ == "__main__":
    unittest.main()
