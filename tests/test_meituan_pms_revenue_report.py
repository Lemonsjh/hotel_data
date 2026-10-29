"""CW04 映射 RS01，验证营业日、科目、首次窗口、历史保留及真实事务。"""
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
import revenue_report as report


class Database:
    def __init__(self, row):
        self.db = sqlite3.connect(":memory:")
        columns = ', '.join(f"`{key}` TEXT" for key in [*row, "room_type_id"])
        self.db.execute(f"CREATE TABLE {report.TABLE} ({columns}, "
                        "PRIMARY KEY(hotel_id,order_id,business_date,room_no,charge_subject))")
        self.db.execute("CREATE TABLE hotel_room_type_mapping (hotel_id TEXT,source_platform TEXT,is_active INT,"
                        "source_product_id TEXT,source_room_type_name TEXT,room_type_id TEXT)")

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        self.snapshot_query = sql.startswith("SELECT source_platform,")
        params = [value.isoformat() if isinstance(value, date) else value for value in params]
        self.result = self.db.execute(sql.replace(" FOR UPDATE", "").replace("%s", "?"), params)

    def fetchall(self):
        rows = self.result.fetchall()
        return [(source, datetime.fromisoformat(stamp)) for source, stamp in rows] if self.snapshot_query else rows

    def fetchone(self):
        return self.result.fetchone()

    def executemany(self, sql, rows):
        sql = re.sub(r"%\((\w+)\)s", r":\1", sql)
        sql = sql.replace("ON DUPLICATE KEY UPDATE", "ON CONFLICT(hotel_id,order_id,business_date,room_no,charge_subject) DO UPDATE SET")
        sql = re.sub(r"VALUES\((\w+)\)", r"excluded.\1", sql)
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
        return self.db.execute(f"SELECT * FROM {report.TABLE} ORDER BY hotel_id,business_date,room_no,charge_subject").fetchall()


class RevenueTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 28, 17)
        self.start, self.end = date(2026, 10, 1), date(2026, 10, 27)
        self.item = {"businessDate": "2026-10-27", "roomNumber": "101", "roomType": "测试大床房",
                     "customerName": "测试客人", "customerCategory": "测试客源", "checkinTime": "2026-10-28 01:00:00",
                     "departureTime": "2026-10-28 14:00:00", "marketPrice": "588.00", "roomRateType": "测试价",
                     "actualPrice": "150.50", "checkinType": "全天", "roomPriceCategory": "夜审房费",
                     "roomNights": "1.00", "roomFee": "150.50", "operator": "测试操作员", "orderNumber": "TEST-ORDER"}
        self.items = [self.item, dict(self.item, roomPriceCategory="钟点房", checkinType="钟点二小时",
                                     actualPrice="70.00", roomNights="0.50", roomFee="70.00")]

    def payload(self, start=None, end=None, items=None):
        return {"code": 10000, "data": {"variables": {"orgId": "123", "startDate": str(start or self.start),
                "endDate": str(end or self.end)}, "dataList": self.items if items is None else items}}

    def rows(self):
        return report.transform(self.payload(), "123", self.start, self.end, self.now, "hotel", "测试酒店")

    def database(self):
        db = Database(self.rows()[0])
        self.addCleanup(db.db.close)
        return db

    def test_native_business_date_price_and_distinct_charge_subjects(self):
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["business_date"], date(2026, 10, 27))
        self.assertEqual(rows[0]["checkin_time"].date(), date(2026, 10, 28))
        self.assertEqual(rows[0]["room_fee"], Decimal("150.50"))
        self.assertEqual(rows[0]["room_daily_price"], Decimal("150.50"))
        self.assertEqual(rows[1]["room_nights"], Decimal("0.50"))
        self.assertEqual([row["order_id"] for row in rows], ["TEST-ORDER"] * 2)
        self.assertEqual([row["charge_subject"] for row in rows], ["夜审房费", "钟点房"])
        self.assertIsNone(rows[0]["channel_unit_id"])
        self.assertNotIn("url", rows[0])

    def test_initial_previous_three_months_and_current_month_incremental(self):
        windows = report.collection_windows(self.now.date(), True)
        self.assertEqual((len(windows), windows[0], windows[-1]),
                         (4, (date(2026, 7, 1), date(2026, 7, 31)), (self.start, self.end)))
        self.assertEqual(report.collection_windows(self.now.date(), False), [(self.start, self.end)])
        first = report.collection_windows(date(2027, 1, 1), True)
        self.assertEqual((len(first), first[0], first[-1]),
                         (3, (date(2026, 10, 1), date(2026, 10, 31)), (date(2026, 12, 1), date(2026, 12, 31))))
        self.assertEqual(report.collection_windows(date(2024, 3, 1), False), [(date(2024, 2, 1), date(2024, 2, 29))])

    def test_initial_detection_is_only_current_hotel_and_meituan_source(self):
        db = self.database()
        row = self.rows()[0]
        db.seed(dict(row, source_platform="其他PMS"))
        db.seed(dict(row, hotel_id="other"))
        self.assertTrue(report.needs_initial_backfill("hotel", db))
        db.seed(dict(row, room_no="102"))
        self.assertFalse(report.needs_initial_backfill("hotel", db))

    def test_empty_response_requires_correct_hotel_dates_and_list_shape(self):
        response = self.payload(items=[])
        self.assertEqual(report.transform(response, "123", self.start, self.end, self.now, "hotel", "酒店"), [])
        for field, value in (("orgId", "999"), ("startDate", "2026-09-01")):
            broken = copy.deepcopy(response)
            broken["data"]["variables"][field] = value
            with self.assertRaises(ValueError):
                report.transform(broken, "123", self.start, self.end, self.now, "hotel", "酒店")
        response["data"].pop("dataList")
        with self.assertRaises(ValueError):
            report.transform(response, "123", self.start, self.end, self.now, "hotel", "酒店")

    def test_invalid_metrics_key_dates_duplicates_and_incomplete_list_are_rejected(self):
        for field, value in (("businessDate", "2026-10-28"), ("businessDate", None), ("roomNumber", ""),
                             ("orderNumber", None), ("roomPriceCategory", ""), ("roomFee", None),
                             ("roomNights", "NaN"), ("actualPrice", True), ("checkinTime", "bad")):
            self.setUp()
            self.item[field] = value
            with self.assertRaises(ValueError):
                self.rows()
        self.setUp()
        self.items.append(dict(self.item))
        with self.assertRaisesRegex(ValueError, "唯一键重复"):
            self.rows()
        response = self.payload()
        response["data"]["totalCount"] = 999
        with self.assertRaises(ValueError):
            report.transform(response, "123", self.start, self.end, self.now, "hotel", "酒店")

    def test_zero_and_negative_adjustments_are_native_values(self):
        self.item.update(roomFee="-150.50", roomNights="-1", actualPrice=None)
        self.items[1].update(roomFee="0", roomNights="0")
        rows = self.rows()
        self.assertEqual((rows[0]["room_fee"], rows[0]["room_nights"]), (Decimal("-150.50"), -1))
        self.assertIsNone(rows[0]["room_daily_price"])
        self.assertEqual(rows[1]["room_fee"], 0)

    def test_backfill_spaced_requests_empty_months_and_verified_filters(self):
        def response(session, method, url, *, json):
            return self.payload(json["startDate"], json["endDate"], [] if json["endDate"] < "2026-10-01" else self.items)
        wait = MagicMock()
        with patch.object(report, "request_json", side_effect=response) as request:
            rows = report.collect("cookie", "hotel", "酒店", "123", initial=True, session=MagicMock(),
                                  clock=lambda: self.now, wait=wait)
        self.assertEqual((request.call_count, wait.call_count, len(rows)), (4, 3, 2))
        body = request.call_args.kwargs["json"]
        self.assertEqual((body["orgId"], body["isCash"], body["checkinTypes"], body["roomNumbers"]), ("123", False, [], []))

    def test_failure_and_cross_midnight_do_not_retry_or_return_partial_batch(self):
        with patch.object(report, "request_json", side_effect=RuntimeError("failed")) as request, self.assertRaises(RuntimeError):
            report.collect("cookie", "hotel", "酒店", "123", initial=True, session=MagicMock(), clock=lambda: self.now)
        request.assert_called_once()
        first = self.payload(date(2026, 7, 1), date(2026, 7, 31), [dict(self.item, businessDate="2026-07-15")])
        with patch.object(report, "request_json", side_effect=[first, RuntimeError("failed")]) as request, self.assertRaises(RuntimeError):
            report.collect("cookie", "hotel", "酒店", "123", initial=True, session=MagicMock(),
                           clock=lambda: self.now, wait=MagicMock())
        self.assertEqual(request.call_count, 2)
        clock = iter([self.now, self.now + timedelta(days=1)])
        with patch.object(report, "request_json", return_value=self.payload()), self.assertRaises(ValueError):
            report.collect("cookie", "hotel", "酒店", "123", initial=False, session=MagicMock(), clock=lambda: next(clock))

    def test_upsert_keeps_history_and_subjects_with_exact_alias_mapping(self):
        db = self.database()
        rows = self.rows()
        db.seed(dict(rows[0], room_fee=Decimal("1")))
        db.seed(dict(rows[0], business_date=date(2026, 1, 1)))
        db.seed(dict(rows[0], hotel_id="other"))
        db.db.executemany("INSERT INTO hotel_room_type_mapping VALUES ('hotel','pms_meituan',1,'',?,?)",
                          [("测试大床房", "ROOM-1"), ("另一个别名", "ROOM-1"), ("测试大床房 ", "WRONG")])
        db.commit()
        for _ in range(2):
            report.sync_mysql(rows, "hotel", self.now, initial=False, connection=db)
        self.assertEqual(len(db.contents()), 4)
        saved = db.db.execute(f"SELECT charge_subject,room_fee,room_type_id FROM {report.TABLE} "
                              "WHERE hotel_id='hotel' AND business_date='2026-10-27'").fetchall()
        self.assertEqual({key: (fee, room_id) for key, fee, room_id in saved},
                         {"夜审房费": ("150.50", "ROOM-1"), "钟点房": ("70.00", "ROOM-1")})

    def test_ambiguous_alias_is_null_and_empty_results_do_not_write(self):
        connection = MagicMock()
        cur = connection.cursor.return_value.__enter__.return_value
        cur.fetchall.side_effect = [[], [("测试大床房", "ROOM-1"), ("测试大床房", "ROOM-2")]]
        report.sync_mysql(self.rows(), "hotel", self.now, initial=False, connection=connection)
        self.assertTrue(all(row["room_type_id"] is None for row in cur.executemany.call_args.args[1]))
        empty = MagicMock()
        report.sync_mysql([], "hotel", self.now, initial=False, connection=empty)
        empty.cursor.assert_not_called()

    def test_wrong_hotel_source_date_and_duplicate_batch_never_start_write(self):
        rows = self.rows()
        for batch in ([dict(row, hotel_id="other") for row in rows], [dict(row, source_platform="other") for row in rows],
                      [dict(row, business_date=self.now.date()) for row in rows], rows + [rows[0]]):
            connection = MagicMock()
            with self.assertRaises(ValueError):
                report.sync_mysql(batch, "hotel", self.now, initial=False, connection=connection)
            connection.cursor.assert_not_called()

    def test_source_collision_newer_batch_and_write_failure_roll_back(self):
        for case in ("source", "newer", "insert"):
            db = self.database()
            row = dict(self.rows()[0], room_fee=Decimal("1"))
            if case == "source":
                row["source_platform"] = "其他PMS"
            elif case == "newer":
                row["snapshot_time"] += timedelta(seconds=1)
            db.seed(row)
            before = db.contents()
            if case == "insert":
                db.db.execute(f"CREATE TRIGGER reject_hour BEFORE INSERT ON {report.TABLE} "
                              "WHEN NEW.charge_subject='钟点房' BEGIN SELECT RAISE(ABORT, 'failed'); END")
            with self.assertRaises((ValueError, sqlite3.IntegrityError)):
                report.sync_mysql(self.rows(), "hotel", self.now, initial=False, connection=db)
            self.assertEqual(db.contents(), before)

    def test_dry_run_no_database_detection_or_write_and_portable_entry(self):
        with patch.object(report, "collect", return_value=self.rows()) as collect, \
                patch.object(report, "needs_initial_backfill") as detect, patch.object(report, "sync_mysql") as sync:
            self.assertEqual(report.main(["--dry-run"]), 0)
        self.assertFalse(collect.call_args.kwargs["initial"])
        detect.assert_not_called()
        sync.assert_not_called()
        result = subprocess.run([sys.executable, str(ROOT / "meituan_pms/revenue_report.py"), "--help"], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
