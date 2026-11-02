"""JD02 主客和同住人逐条保存、昨日离店窗口、60天保留及事务回滚。"""
import copy
import re
import sqlite3
import subprocess
import sys
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "meituan_pms"), str(ROOT / "OTA采集服务")]
import guest_report as report
import collection_schema


class Database:
    def __init__(self, row):
        self.db = sqlite3.connect(":memory:")
        columns = ", ".join(f"`{key}` TEXT" for key in [*row, "room_type_id"])
        self.db.execute(f"CREATE TABLE {report.TABLE} (id INTEGER PRIMARY KEY AUTOINCREMENT, {columns})")
        self.db.execute("CREATE TABLE hotel_room_type_mapping (hotel_id TEXT,source_platform TEXT,is_active INT,"
                        "source_product_id TEXT,source_room_type_name TEXT,room_type_id TEXT)")

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        values = [value.isoformat() if isinstance(value, date) else value for value in params]
        self.result = self.db.execute(sql.replace("%s", "?"), values)

    def fetchall(self):
        return self.result.fetchall()

    def executemany(self, sql, rows):
        sql = re.sub(r"%\((\w+)\)s", r":\1", sql)
        values = [{key: value.isoformat(sep=" ") if isinstance(value, datetime)
                   else value.isoformat() if isinstance(value, date)
                   else str(value) if isinstance(value, Decimal) else value for key, value in row.items()} for row in rows]
        self.db.executemany(sql, values)

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()

    def seed(self, row):
        self.executemany(f"INSERT INTO {report.TABLE} ({', '.join(row)}) VALUES "
                         f"({', '.join('%(' + key + ')s' for key in row)})", [row])
        self.commit()

    def contents(self):
        return self.db.execute(f"SELECT * FROM {report.TABLE} ORDER BY hotel_id,source_platform,business_date,order_id").fetchall()


class GuestReportTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 30, 16)
        self.day = date(2026, 10, 29)
        self.main = {"orderSn": "TEST-ORDER", "roomNumber": "101", "roomType": "测试房型",
                     "customerId": 2116070027978715167, "customerName": "测试主客", "idCardNumber": "TEST****1234",
                     "customerMobile": "", "price": "148.00", "arrivalTime": "2026-10-29 15:29:44",
                     "departureTime": "2026-10-29 18:00:00", "checkinType": "全天", "orderSource": "门店",
                     "source": "散客", "memberLevelId": "123", "memberLevel": "金卡", "oneselfOrNot": "非",
                     "priceStackeholderName": "", "priceStackeholderMobile": None, "orderMemo": "",
                     "checkOutTip": None, "checkinStatus": "在住", "operator": "测试操作员", "totalCheckinCount": None,
                     "roomAttr": None, "mainCheckinId": 2116070028037476437, "mainCheckinPerson": 1}
        self.companion = dict(self.main, customerId=2116070028479086676, customerName="测试同住人", mainCheckinPerson=0)
        self.payload = {"code": 10000, "data": {"variables": {"orgId": "123", "startDate": str(self.day),
                        "endDate": str(self.day)}, "dataList": [self.companion, self.main]}}

    def rows(self):
        return report.transform(self.payload, "123", self.day, self.now, "hotel", "测试酒店")

    def database(self):
        db = Database(self.rows()[1])
        self.addCleanup(db.db.close)
        return db

    def test_one_order_keeps_both_main_and_companion_with_original_fields(self):
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual([row["order_id"] for row in rows], ["TEST-ORDER", "TEST-ORDER"])
        self.assertEqual([row["main_checkin_person"] for row in rows], [0, 1])
        self.assertEqual(rows[0]["customer_name"], "测试同住人")
        main = rows[1]
        self.assertEqual((main["customer_name"], main["member_level"], main["room_price"]),
                         ("测试主客", "金卡", Decimal("148.00")))
        self.assertEqual(main["customer_id"], "2116070027978715167")
        self.assertEqual(main["main_checkin_id"], "2116070028037476437")
        self.assertEqual(main["id_card_number"], "TEST****1234")
        self.assertIsNone(main["customer_mobile"])
        self.assertEqual(main["business_date"], self.day)

    def test_multiple_main_guests_and_duplicate_records_are_not_merged(self):
        earlier = dict(self.main, arrivalTime="2026-10-29 10:00:00", price="90.50", customerName="较早主客")
        other = dict(self.main, orderSn="SECOND-ORDER")
        self.payload["data"]["dataList"] += [earlier, other, dict(self.main)]
        rows = self.rows()
        self.assertEqual(len(rows), 5)
        self.assertEqual((rows[2]["customer_name"], rows[2]["room_price"]),
                         ("较早主客", Decimal("90.50")))
        self.payload["data"]["dataList"].reverse()
        self.assertEqual(self.rows(), list(reversed(rows)))

    def test_request_only_queries_yesterday_with_provided_filters_and_no_retry(self):
        with patch.object(report, "request_json", return_value=self.payload) as request:
            rows, snapshot = report.collect("cookie", "hotel", "测试酒店", "123", session=MagicMock(), clock=lambda: self.now)
        request.assert_called_once()
        body = request.call_args.kwargs["json"]
        self.assertEqual((body["dateCategory"], body["dateType"], body["startDate"], body["endDate"]),
                         ("0", "2", "2026-10-29", "2026-10-29"))
        self.assertEqual(body["checkinStatusList"], [])
        self.assertFalse(body["showTotalCheckinCount"])
        self.assertEqual((len(rows), snapshot), (2, self.now))
        with patch.object(report, "request_json", side_effect=RuntimeError("failed")) as request:
            with self.assertRaises(RuntimeError):
                report.collect("cookie", "hotel", "测试酒店", "123", session=MagicMock(), clock=lambda: self.now)
        request.assert_called_once()

    def test_invalid_response_fails_and_empty_or_companion_only_are_valid(self):
        original = copy.deepcopy(self.payload)
        for change in (lambda: self.payload["data"]["variables"].update(orgId="999"),
                       lambda: self.payload["data"]["variables"].update(endDate="2026-10-31"),
                       lambda: self.payload["data"].update(totalCount=3),
                       lambda: self.payload["data"].pop("dataList"),
                       lambda: self.payload["data"]["dataList"][1].update(departureTime="2026-10-30 10:00:00"),
                       lambda: self.payload["data"]["dataList"][1].pop("mainCheckinPerson")):
            self.payload = copy.deepcopy(original)
            change()
            with self.assertRaises(ValueError):
                self.rows()
        for items in ([], [self.companion]):
            self.payload = copy.deepcopy(original)
            self.payload["data"]["dataList"] = items
            self.assertEqual(len(self.rows()), len(items))

    def test_business_day_preserves_next_day_early_departure_without_changing_archive_date(self):
        self.payload["data"]["variables"]["endDate"] = "2026-10-30"
        self.assertEqual(len(self.rows()), 2)
        self.main["departureTime"] = "2026-10-30 02:00:00"
        row = self.rows()[1]
        self.assertEqual(row["departure_time"], datetime(2026, 10, 30, 2))
        self.assertEqual(row["business_date"], self.day)
        self.main["departureTime"] = "2026-10-31 00:00:00"
        with self.assertRaises(ValueError):
            self.rows()

    def test_earlier_arrivals_are_valid_but_missing_or_outside_departures_fail(self):
        self.main["arrivalTime"] = "2026-10-01 15:00:00"
        self.assertEqual(self.rows()[1]["arrival_time"], datetime(2026, 10, 1, 15))
        for value in (None, "2026-10-28 14:00:00", "2026-10-30 14:00:00"):
            self.main["departureTime"] = value
            with self.assertRaises(ValueError):
                self.rows()

    def test_refresh_is_idempotent_and_sixty_days_boundary_and_scope_are_preserved(self):
        db, row = self.database(), self.rows()[1]
        cutoff = self.day - timedelta(days=59)
        for saved in (dict(row, customer_name="旧主客"), dict(row, business_date=cutoff),
                      dict(row, business_date=cutoff - timedelta(days=1)), dict(row, business_date=self.day - timedelta(days=1)),
                      dict(row, hotel_id="other", business_date=cutoff - timedelta(days=1)),
                      dict(row, source_platform="其他PMS", business_date=cutoff - timedelta(days=1))):
            db.seed(saved)
        db.db.execute("INSERT INTO hotel_room_type_mapping VALUES ('hotel','pms_meituan',1,'','测试房型','ROOM-1')")
        db.commit()
        for _ in range(2):
            report.sync_mysql(self.rows(), "hotel", self.now, db)
        self.assertEqual(len(db.contents()), 6)
        current = db.db.execute(f"SELECT customer_name,room_type_id FROM {report.TABLE} "
                                "WHERE hotel_id='hotel' AND source_platform=? AND business_date=? ORDER BY main_checkin_person",
                                (report.SOURCE, str(self.day))).fetchall()
        self.assertEqual(current, [("测试同住人", "ROOM-1"), ("测试主客", "ROOM-1")])
        self.assertEqual((self.day - cutoff).days + 1, 60)

    def test_ambiguous_or_inexact_room_alias_is_not_guessed(self):
        db = self.database()
        db.db.executemany("INSERT INTO hotel_room_type_mapping VALUES ('hotel','pms_meituan',1,'',?,?)",
                          [("测试房型", "A"), ("测试房型", "B"), ("测试房型 ", "C")])
        db.commit()
        report.sync_mysql(self.rows(), "hotel", self.now, db)
        self.assertEqual(db.db.execute(f"SELECT room_type_id FROM {report.TABLE}").fetchall(), [(None,), (None,)])

    def test_insert_or_cleanup_failure_rolls_back_refresh_and_history(self):
        for event in ("INSERT", "DELETE"):
            db, row = self.database(), self.rows()[1]
            db.seed(dict(row, customer_name="原主客"))
            db.seed(dict(row, business_date=self.day - timedelta(days=60)))
            before = db.contents()
            condition = "" if event == "INSERT" else f"WHEN OLD.business_date < '{self.day}'"
            db.db.execute(f"CREATE TRIGGER reject_write BEFORE {event} ON {report.TABLE} {condition} "
                          "BEGIN SELECT RAISE(ABORT, 'failed'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                report.sync_mysql(self.rows(), "hotel", self.now, db)
            self.assertEqual(db.contents(), before)

    def test_empty_snapshot_refreshes_yesterday_and_cleans_only_expired_current_scope(self):
        db, row = self.database(), self.rows()[0]
        for saved in (row, dict(row, business_date=self.day - timedelta(days=60)),
                      dict(row, business_date=self.day - timedelta(days=1)), dict(row, hotel_id="other")):
            db.seed(saved)
        report.sync_mysql([], "hotel", self.now, db)
        self.assertEqual(len(db.contents()), 2)

    def test_cross_year_and_leap_day_request_and_retention(self):
        for snapshot, day in ((datetime(2027, 1, 1), date(2026, 12, 31)), (datetime(2024, 3, 1), date(2024, 2, 29))):
            connection = MagicMock()
            report.sync_mysql([], "hotel", snapshot, connection)
            cursor = connection.cursor.return_value.__enter__.return_value
            self.assertEqual(cursor.execute.call_args_list[0].args[1][-1], day)
            self.assertEqual(cursor.execute.call_args.args[1][-1], day - timedelta(days=59))
            self.assertEqual(report.request_body("123", day)["startDate"], day.isoformat())

    def test_dry_run_never_writes_schema_covers_columns_and_portable_script_loads(self):
        with patch.object(report, "collect", return_value=(self.rows(), self.now)), patch.object(report, "sync_mysql") as sync:
            self.assertEqual(report.main(["--dry-run"]), 0)
        sync.assert_not_called()
        ddl = collection_schema.table_definitions()[report.TABLE]
        for column in [*self.rows()[0], "room_type_id"]:
            self.assertIn(f"`{column}`", ddl)
        process = subprocess.run([sys.executable, str(ROOT / "meituan_pms/guest_report.py"), "--help"], capture_output=True, timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
