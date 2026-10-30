from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch


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

    def test_login_check_runs_before_order_request_and_failure_stops_writes(self):
        with patch.object(self.module, "FlowBrowserClient") as browser, \
                patch.object(self.module, "fetch_orders") as fetch, \
                patch.object(self.module, "sync_order_detail_history") as sync:
            client = browser.return_value.__enter__.return_value
            client.page.url = "https://ebooking.ctrip.com/login"
            with self.assertRaisesRegex(RuntimeError, "login session"):
                self.module.main()
            fetch.assert_not_called()
            sync.assert_not_called()
            browser.return_value.__exit__.assert_called_once()

    def test_client_name_is_saved_and_missing_name_remains_null(self):
        with patch.dict("os.environ", {"HOTEL_ID": "test"}):
            rows = self.module.build_rows([
                {"formId": 1, "arrival": "2026-10-30", "clientName": "测试客人"},
                {"formId": 2, "arrival": "2026-10-30"},
            ], datetime(2026, 10, 30))
        self.assertEqual(len(rows[0]), len(self.module.HEADERS))
        names = [dict(zip(self.module.HEADERS, row))["client_name"] for row in rows]
        self.assertEqual(names, ["测试客人", None])

    def test_existing_order_table_gains_name_and_amount_only_once(self):
        import ota_mysql_writer as writer
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        columns = {key: "varchar" for key in self.module.HEADERS
                   if key not in {"client_name", *self.module.AMOUNT_FIELDS}}
        with patch.dict("os.environ", {"HOTEL_ID": "test"}):
            rows = self.module.build_rows([
                {"formId": 1, "arrival": "2026-10-30", "clientName": "测试客人"}
            ], datetime(2026, 10, 30))
        with patch.object(writer, "connect_mysql", return_value=connection), \
                patch.object(writer, "load_column_types", return_value=columns):
            for _ in range(2):
                writer.sync_order_detail_history(self.module.TABLE_NAME, self.module.HEADERS,
                                                 rows, "test", date(2026, 10, 1))
        alters = [call for call in cursor.execute.call_args_list if call.args[0].startswith("ALTER TABLE")]
        self.assertEqual(len(alters), 5)
        self.assertIn("`client_name`", alters[0].args[0])
        self.assertIn("`order_amount`", alters[1].args[0])
        self.assertIn("`client_name`=VALUES(`client_name`)", cursor.executemany.call_args.args[0])
        self.assertIn("`order_amount`=VALUES(`order_amount`)", cursor.executemany.call_args.args[0])
        for column in self.module.AMOUNT_FIELDS:
            self.assertTrue(any(f"`{column}`" in call.args[0] for call in alters))
            self.assertIn(f"`{column}`=VALUES(`{column}`)", cursor.executemany.call_args.args[0])

    def test_amount_comes_from_detail_and_zero_and_missing_are_distinct(self):
        client = MagicMock()
        client.post_json.side_effect = [
            {"resStatus": {"rcode": 200}, "detail": {
                "amount": "1,234.50", "originTotalPrice": "2000.00",
                "totalPrice": "1800.50", "originTotalCost": "1600.00"}},
            {"resStatus": {"rcode": 200}, "detail": {key: 0 for key in self.module.AMOUNT_FIELDS.values()}},
            {"resStatus": {"rcode": 200}, "detail": {}},
        ]
        items = [{"hotel": 9, "formId": i, "orderId": str(i), "arrival": "2026-10-30",
                  "sourceType": "Ebooking", "ctripSourceType": "ctrip"} for i in range(1, 4)]
        with patch.object(self.module.time, "sleep") as sleep, patch.dict("os.environ", {"HOTEL_ID": "test"}):
            self.module.fetch_order_amounts(client, items)
            rows = self.module.build_rows(items, datetime(2026, 10, 30))
        amounts = [dict(zip(self.module.HEADERS, row))["order_amount"] for row in rows]
        self.assertEqual(amounts, [Decimal("1234.50"), Decimal(0), None])
        for column, expected in (("origin_total_price", "2000.00"), ("total_price", "1800.50"),
                                 ("origin_total_cost", "1600.00")):
            self.assertEqual([dict(zip(self.module.HEADERS, row))[column] for row in rows],
                             [Decimal(expected), Decimal(0), None])
        self.assertEqual(sleep.call_count, 2)
        self.assertTrue(all(c.args[0] == self.module.DETAIL_API_URL for c in client.post_json.call_args_list))
        self.assertEqual(client.post_json.call_args_list[0].args[1]["formID"], 1)
        with tempfile.TemporaryDirectory() as directory, patch.object(self.module, "OUTPUT_DIR", Path(directory)):
            self.module.write_output(rows, date(2026, 10, 1), date(2026, 10, 30), datetime(2026, 10, 30))
            saved = json.loads((Path(directory) / "ctrip_ota_order_detail.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["rows"][0]["order_amount"], "1234.50")
            self.assertEqual(saved["rows"][0]["origin_total_price"], "2000.00")
            self.assertEqual(saved["rows"][0]["total_price"], "1800.50")
            self.assertEqual(saved["rows"][0]["origin_total_cost"], "1600.00")

    def test_amount_request_failure_stops_database_and_file_write(self):
        with patch.object(self.module, "FlowBrowserClient") as browser, \
                patch.object(self.module, "ensure_logged_in"), \
                patch.object(self.module, "fetch_orders", return_value=[{"hotel": 9, "formId": 1, "orderId": "1"}]), \
                patch.object(self.module, "sync_order_detail_history") as sync, \
                patch.object(self.module, "write_output") as output:
            browser.return_value.__enter__.return_value.post_json.return_value = {"resStatus": {"rcode": 500}}
            with self.assertRaisesRegex(RuntimeError, "order-detail"):
                self.module.main()
            sync.assert_not_called()
            output.assert_not_called()


if __name__ == "__main__":
    unittest.main()
