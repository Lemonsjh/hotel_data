"""普通订单的分页、会员转换和公共写入事务回归。"""
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "美团OTA数据采集代码"), str(ROOT)]
import meituan_order_detail_data as data
import ota_mysql_writer as writer

NOW = datetime(2026, 10, 30, 10, 30)
START, END = date(2026, 10, 1), date(2026, 10, 30)


def item(order_id="1234567890123456789", **fields):
    return {"orderId": order_id, "poiId": 42, "partnerId": 43, "status": "ACCEPTED",
            "bookingTimeString": "2026-10-30 09:30:00", "checkInDateString": "2026-10-30",
            "checkOutDateString": "2026-10-31", "price": 12345, "totalFee": 0,
            "memberLevel": 3, "memberBenefits": 0, "memberMark": 1, **fields}


class OrderTests(unittest.TestCase):
    def setUp(self):
        for target, value in (("POI_ID", "42"), ("PARTNER_ID", "43"), ("MEITUAN_EB_COOKIE", "fake")):
            p = patch.object(data, target, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.dict(os.environ, {"HOTEL_ID": "hotel"})
        p.start()
        self.addCleanup(p.stop)

    def test_window_and_request_dates(self):
        self.assertEqual(data.report_window(NOW), (START, END))
        params = data.request_params(START, END, 20)
        self.assertEqual((params["offset"], params["searchTimeType"], params["filter"]), (20, 2, "ALL"))
        self.assertEqual(params["endTime"] - params["startTime"] + 1, 30 * 86400000)

    def test_pagination_and_empty_result(self):
        with patch.object(data, "PAGE_SIZE", 2), patch.object(data.time, "sleep") as sleep, \
                patch.object(data, "request_page", side_effect=[
                    {"total": 3, "results": [item("1"), item("2")]},
                    {"total": 3, "results": [item("3")]},
                ]) as request:
            self.assertEqual(len(data.fetch_orders(START, END)), 3)
            self.assertEqual([call.args[-1] for call in request.call_args_list], [0, 2])
            sleep.assert_called_once_with(1)
        with patch.object(data, "request_page", return_value={"total": 0, "results": []}):
            self.assertEqual(data.fetch_orders(START, END), [])

    def test_incomplete_pages_and_wrong_hotel_do_not_pass(self):
        for result in ({"total": 3, "results": [item()]}, {"total": 1, "results": [item(poiId=99)]}):
            with patch.object(data, "request_page", return_value=result), self.assertRaises(RuntimeError):
                data.fetch_orders(START, END)

    def test_member_fields_and_money(self):
        row = data.build_rows([item()], NOW)[0]
        self.assertEqual(row["order_id"], "1234567890123456789")
        self.assertEqual((row["member_level_name"], row["member_benefits"], row["member_mark"]), ("黄金会员", 0, 1))
        self.assertEqual(row["order_price"], Decimal("123.45"))
        self.assertEqual(row["total_fee"], 0)
        self.assertIsNone(row["commission"])
        for level in (None, 0, 7):
            row = data.build_rows([item(memberLevel=level)], NOW)[0]
            self.assertEqual(row["member_level"], level)
            self.assertIsNone(row["member_level_name"])
        self.assertEqual(data.time_value(1793318400000), datetime(2026, 10, 30, 8))

    def test_api_failure_and_dry_run_do_not_write(self):
        client = MagicMock()
        client.get.return_value.status_code = 200
        client.get.return_value.json.return_value = {"status": 50101}
        with self.assertRaises(RuntimeError):
            data.request_page(client, START, END, 0)
        with tempfile.TemporaryDirectory() as directory, patch.object(data, "OUTPUT_DIR", Path(directory)), \
                patch.object(data, "fetch_orders", return_value=[item()]), patch.object(data, "sync_orders") as sync:
            data.main(["--dry-run"])
            sync.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])
        with patch.object(data, "fetch_orders", side_effect=RuntimeError("failed")), \
                patch.object(data, "sync_orders") as sync:
            with self.assertRaises(RuntimeError):
                data.main([])
            sync.assert_not_called()

    def test_shared_writer_upsert_cleanup_and_rollback(self):
        row = data.build_rows([item()], NOW)[0]
        db = Database(row)
        self.addCleanup(db.db.close)
        db.seed({**row, "order_id": "expired", "booking_time": datetime(2026, 9, 30)})
        db.seed({**row, "order_id": "boundary", "booking_time": datetime(2026, 10, 1)})
        db.seed({**row, "order_id": "other", "hotel_id": "other", "booking_time": datetime(2026, 9, 30)})
        db.seed({**row, "order_id": "other-poi", "poi_id": "99", "booking_time": datetime(2026, 9, 30)})
        with patch.object(writer, "connect_mysql", return_value=db), \
                patch.object(writer, "load_column_types", return_value={key: "varchar" for key in row}):
            for level in (2, 4):
                data.sync_orders([{**row, "member_level": level}], START)
            actual = dict(db.db.execute(f"SELECT order_id,member_level FROM {data.TABLE_NAME}"))
            self.assertEqual(actual, {row["order_id"]: "4", "boundary": "3", "other": "3", "other-poi": "3"})
            db.fail_delete = True
            with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                data.sync_orders([{**row, "member_level": 6}], START)
            self.assertEqual(db.db.execute(f"SELECT member_level FROM {data.TABLE_NAME} WHERE order_id=?",
                                          (row["order_id"],)).fetchone()[0], "4")
        with patch.object(data, "sync_order_detail_history") as sync:
            data.sync_orders([], START)
            sync.assert_not_called()


class Database:
    def __init__(self, row):
        self.db = sqlite3.connect(":memory:")
        columns = ",".join(f"`{key}` TEXT" for key in row)
        self.db.execute(f"CREATE TABLE {data.TABLE_NAME} ({columns}, PRIMARY KEY(hotel_id,order_id))")
        self.fail_delete = False

    def cursor(self): return self
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def close(self): pass
    def commit(self): self.db.commit()
    def rollback(self): self.db.rollback()

    def seed(self, row):
        self.db.execute(f"INSERT INTO {data.TABLE_NAME} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
                        tuple(str(v) if isinstance(v, (date, Decimal)) else v for v in row.values()))
        self.db.commit()

    def executemany(self, sql, rows):
        sql = sql.replace("ON DUPLICATE KEY UPDATE", "ON CONFLICT(hotel_id,order_id) DO UPDATE SET")
        sql = re.sub(r"VALUES\(`([^`]+)`\)", r"excluded.`\1`", sql).replace("%s", "?")
        self.db.executemany(sql, [tuple(str(v) if isinstance(v, Decimal) else v for v in row) for row in rows])

    def execute(self, sql, params):
        if self.fail_delete:
            raise RuntimeError("cleanup failed")
        cursor = self.db.execute(sql.replace("%s", "?"), tuple(str(v) if isinstance(v, date) else v for v in params))
        self.rowcount = cursor.rowcount


if __name__ == "__main__":
    unittest.main()
