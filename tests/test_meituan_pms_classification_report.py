"""美团 PMS JL11 原生汇总、完整性校验、来源隔离和真实事务回滚。"""
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
import classification_report as report


class Database:
    def __init__(self, rows):
        self.db = sqlite3.connect(":memory:")
        columns = [*rows[0], "room_type_id"]
        fields = ', '.join(f"`{key}` TEXT" for key in columns)
        self.db.execute(f"CREATE TABLE {report.TABLE} ({fields}, "
                        "PRIMARY KEY(hotel_id,snapshot_date,section,room_type_name,dimension_code))")
        self.db.execute("CREATE TABLE hotel_room_type_mapping (hotel_id TEXT,source_platform TEXT,"
                        "is_active INT,source_product_id TEXT,source_room_type_name TEXT,room_type_id TEXT)")

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=()):
        self.snapshot_query = sql.startswith("SELECT snapshot_date,")
        self.result = self.db.execute(sql.replace(" FOR UPDATE", "").replace("%s", "?"), params)

    def fetchall(self):
        rows = self.result.fetchall()
        return [(date.fromisoformat(row[0]), *row[1:-1], datetime.fromisoformat(row[-1])) for row in rows] \
            if self.snapshot_query else rows

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
        return self.db.execute(f"SELECT * FROM {report.TABLE} ORDER BY hotel_id,source_platform,room_type_name").fetchall()


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 28, 12)
        self.start, self.end = date(2026, 9, 28), date(2026, 10, 27)
        self.room = {"roomTypeKey": "TYPE-A", "roomTypeName": "测试大床房", "roomCount": "300",
                     "roomPoint": "10.5", "rentRate": "3.50%", "avgRoomPrice": "95.24", "revpar": "3.33",
                     "roomFee": "1,000.00", "nightRoomCount": "10", "nightRentRate": "3.33%",
                     "roomPointRatio": "100.00%", "nightAvgRoomPrice": "100.00", "discountRate": "20%",
                     "url": "/pms-report/home/report/#JY03?orgId=123&startDate=2026-09-28&endDate=2026-10-27"}
        self.other = dict(self.room, roomTypeKey="TYPE-B", roomTypeName="测试双床房", roomCount="600",
                          roomPoint="0", rentRate="0.00%", avgRoomPrice="0", revpar="0", roomFee="0",
                          nightRoomCount="0", nightRentRate="0.00%")
        self.total = dict(self.room, roomTypeKey="总计", roomTypeName="总计", roomCount="900",
                          rentRate="1.17%", revpar="1.11", nightRentRate="1.11%", url=None)
        self.payload = {"code": 10000, "data": {"variables": {"startDate": "2026-09-28", "endDate": "2026-10-27"},
                        "data": {"summaryList": [self.room, self.other, self.total]}}}

    def rows(self):
        return report.transform(self.payload, "123", self.now, "hotel", "测试酒店")

    def database(self):
        db = Database(self.rows())
        self.addCleanup(db.db.close)
        return db

    def test_native_eight_metrics_are_preserved_and_total_is_validation_only(self):
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["room_count"], Decimal("300"))
        self.assertEqual(rows[0]["room_nights"], Decimal("10.5"))
        self.assertEqual(rows[0]["room_revenue"], Decimal("1000"))
        self.assertEqual(rows[0]["occupancy_rate"], Decimal("3.50"))
        self.assertEqual(rows[0]["overnight_occupancy_rate"], Decimal("3.33"))
        self.assertEqual(rows[1]["average_room_price"], 0)
        self.assertNotIn("总计", {row["room_type_name"] for row in rows})
        self.assertNotIn("discount_rate", rows[0])
        self.assertEqual((rows[0]["period_start"], rows[0]["period_end"]), (self.start, self.end))

    def test_one_real_room_is_valid_after_total_validation(self):
        self.payload["data"]["data"]["summaryList"].remove(self.other)
        self.total["roomCount"] = "300"
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        db = self.database()
        report.sync_mysql(rows, db)
        self.assertEqual(len(db.contents()), 1)

    def test_wrong_total_is_still_rejected(self):
        self.total["roomFee"] = "1001"
        with self.assertRaisesRegex(ValueError, "明细与总计不一致"):
            self.rows()

    def test_closed_thirty_day_window_and_request_uses_pms_hotel_id(self):
        self.assertEqual(report.request_body("123", self.now.date()),
                         {"hotelId": "123", "startDate": "2026-09-28", "endDate": "2026-10-27", "searchCategory": []})
        for today in (date(2027, 1, 1), date(2024, 3, 1)):
            start, end = report.report_window(today)
            self.assertEqual((end - start).days + 1, 30)
            self.assertEqual(end, today - timedelta(days=1))
        with self.assertRaises(ValueError):
            report.request_body("invalid", self.now.date())

    def test_wrong_dates_or_hotel_in_variables_or_fragment_links_are_rejected(self):
        for change in (lambda: self.payload["data"]["variables"].update(startDate="2026-10-26"),
                       lambda: self.payload["data"]["variables"].update(hotelId="999"),
                       lambda: self.room.update(url=self.room["url"].replace("orgId=123", "orgId=999")),
                       lambda: self.room.update(url=self.room["url"].replace("2026-10-27", "2026-10-26"))):
            self.setUp()
            change()
            with self.assertRaises(ValueError):
                self.rows()

    def test_missing_duplicate_malformed_empty_and_incomplete_summary_keep_old_data(self):
        for change in (lambda: self.room.pop("roomFee"), lambda: self.room.update(roomFee=None),
                       lambda: self.room.update(rentRate="NaN%"), lambda: self.room.update(roomPoint=True),
                       lambda: self.room.update(roomCount="-1"), lambda: self.room.update(roomTypeName=""),
                       lambda: self.other.update(roomTypeKey="TYPE-A"),
                       lambda: self.other.update(roomTypeName="测试大床房"),
                       lambda: self.payload["data"]["data"]["summaryList"].pop(0),
                       lambda: self.payload["data"]["data"]["summaryList"].pop(),
                       lambda: self.payload["data"]["data"].update(summaryList=[]),
                       lambda: self.payload.update(code=0)):
            self.setUp()
            change()
            with self.assertRaises(ValueError):
                self.rows()

    def test_native_signed_revenue_and_prices_are_not_recomputed(self):
        for item in (self.room, self.total):
            item.update(roomFee="-1000", avgRoomPrice="-95.24", revpar="-3.33")
        self.assertEqual(self.rows()[0]["room_revenue"], -1000)

    def test_collect_posts_once_and_rejects_cross_midnight_or_failure(self):
        session = MagicMock()
        with patch.object(report, "request_json", return_value=self.payload) as request:
            rows = report.collect("cookie", "hotel", "测试酒店", "123", session=session, clock=lambda: self.now)
        self.assertEqual(len(rows), 2)
        request.assert_called_once_with(session, "POST", report.REPORT_URL, json=report.request_body("123", self.now.date()))
        clock = iter([self.now, self.now + timedelta(days=1)])
        with patch.object(report, "request_json", return_value=self.payload), self.assertRaises(ValueError):
            report.collect("cookie", "hotel", "测试酒店", "123", session=session, clock=lambda: next(clock))
        with patch.object(report, "request_json", side_effect=RuntimeError("failed")) as request, self.assertRaises(RuntimeError):
            report.collect("cookie", "hotel", "测试酒店", "123", session=session, clock=lambda: self.now)
        request.assert_called_once()

    def test_transaction_replaces_only_own_latest_batch_and_maps_exact_aliases(self):
        db = self.database()
        rows = self.rows()
        db.seed(dict(rows[0], snapshot_date=self.now.date() - timedelta(days=1)))
        db.seed(dict(rows[0], room_type_name="总计"))
        db.seed(dict(rows[0], source_platform="PMS（其他）", room_type_name="其他房型"))
        db.seed(dict(rows[0], hotel_id="other-hotel"))
        aliases = [("测试大床房", "ROOM-1"), ("另一别名", "ROOM-1"), ("测试双床房", "ROOM-2"),
                   ("测试双床房", "ROOM-3"), ("总计", "WRONG"), ("测试大床房 ", "WRONG")]
        db.db.executemany("INSERT INTO hotel_room_type_mapping VALUES ('hotel','pms_meituan',1,'',?,?)", aliases)
        db.commit()
        report.sync_mysql(rows, db)
        self.assertEqual(len(db.contents()), 4)
        current = db.db.execute(f"SELECT room_type_name,room_type_id FROM {report.TABLE} "
                                "WHERE hotel_id='hotel' AND source_platform=?", (report.SOURCE,)).fetchall()
        self.assertEqual(dict(current), {"测试大床房": "ROOM-1", "测试双床房": None})

    def test_other_source_collision_and_newer_batch_preserve_database(self):
        for newer in (False, True):
            db = self.database()
            row = dict(self.rows()[0], source_platform=report.SOURCE if newer else "PMS（其他）")
            if newer:
                row["snapshot_time"] += timedelta(seconds=1)
            db.seed(row)
            before = db.contents()
            with self.assertRaises(ValueError):
                report.sync_mysql(self.rows(), db)
            self.assertEqual(db.contents(), before)

    def test_failed_insert_rolls_back_actual_delete(self):
        db = self.database()
        db.seed(self.rows()[0])
        db.seed(dict(self.rows()[0], room_type_name="总计"))
        before = db.contents()
        db.db.execute(f"CREATE TRIGGER reject_insert BEFORE INSERT ON {report.TABLE} "
                      "BEGIN SELECT RAISE(ABORT, 'insert failed'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            report.sync_mysql(self.rows(), db)
        self.assertEqual(db.contents(), before)

    def test_bad_batch_or_empty_never_changes_database(self):
        db = self.database()
        db.seed(self.rows()[0])
        before = db.contents()
        rows = self.rows()
        for records in ([], [dict(row, period_start=self.end) for row in rows],
                        [dict(row, source_platform="wrong") for row in rows], rows + [copy.copy(rows[0])],
                        rows + [dict(rows[0], room_type_name="总计")]):
            with self.assertRaises(ValueError):
                report.sync_mysql(records, db)
            self.assertEqual(db.contents(), before)

    def test_dry_run_and_failure_never_call_writer_and_portable_entry_loads(self):
        with patch.object(report, "collect", return_value=self.rows()), patch.object(report, "sync_mysql") as sync:
            self.assertEqual(report.main(["--dry-run"]), 0)
        sync.assert_not_called()
        with patch.object(report, "collect", side_effect=ValueError("invalid")), patch.object(report, "sync_mysql") as sync:
            self.assertEqual(report.main([]), 1)
        sync.assert_not_called()
        result = subprocess.run([sys.executable, str(ROOT / "meituan_pms/classification_report.py"), "--help"],
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
