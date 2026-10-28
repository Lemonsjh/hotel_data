"""JL01 单日请求、去年同期、原生指标、历史保留和事务回滚。"""
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
import room_performance_report as report


class Database:
    def __init__(self, row):
        self.db = sqlite3.connect(":memory:")
        columns = ', '.join(f"`{key}` TEXT" for key in [*row, "room_type_id"])
        self.db.execute(f"CREATE TABLE {report.TABLE} ({columns}, PRIMARY KEY(hotel_id,business_date,room_type_name))")
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

    def executemany(self, sql, rows):
        sql = re.sub(r"%\((\w+)\)s", r":\1", sql)
        sql = sql.replace("ON DUPLICATE KEY UPDATE", "ON CONFLICT(hotel_id,business_date,room_type_name) DO UPDATE SET")
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
        return self.db.execute(f"SELECT * FROM {report.TABLE} ORDER BY hotel_id,business_date,room_type_name").fetchall()


class RoomPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 28, 13)
        self.days = [date(2026, 10, 27), date(2025, 10, 27)]

    def payload(self, day):
        room = {"roomTypeKey": "TYPE-A", "roomTypeName": "测试房型", "roomCount": "10", "roomPoint": "2.5",
                "rentRate": "25.00%", "avgRoomPrice": "40", "revpar": "10", "roomFee": "100",
                "nightRoomCount": "2", "nightRentRate": "20.00%",
                "url": f"/pms-report/#JY03?orgId=123&startDate={day}&endDate={day}"}
        zero = dict(room, roomTypeKey="TYPE-B", roomTypeName="零值房型", roomPoint="0", rentRate="0%",
                    avgRoomPrice="0", revpar="0", roomFee="0", nightRoomCount="0", nightRentRate="0%")
        total = dict(room, roomTypeKey="总计", roomTypeName="总计", roomCount="20", rentRate="12.50%",
                     revpar="5", nightRentRate="10%", url=None)
        return {"code": 10000, "data": {"variables": {"startDate": str(day), "endDate": str(day)},
                "data": {"summaryList": [room, zero, total]}}}

    def rows(self, day):
        return report.transform(self.payload(day), "123", day, self.now, "hotel", "测试酒店")

    def batch(self):
        return [row for day in self.days for row in self.rows(day)]

    def database(self):
        db = Database(self.batch()[0])
        self.addCleanup(db.db.close)
        return db

    def test_dates_and_leap_day_match_existing_jl01(self):
        self.assertEqual(report.collection_dates(self.now.date()), self.days)
        self.assertEqual(report.collection_dates(date(2024, 3, 1)), [date(2024, 2, 29), date(2023, 2, 28)])
        self.assertEqual(report.collection_dates(date(2027, 1, 1)), [date(2026, 12, 31), date(2025, 12, 31)])

    def test_single_day_native_metrics_percentage_and_zero_without_total(self):
        rows = self.rows(self.days[0])
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]["room_nights"], rows[0]["occupancy_rate"], rows[0]["room_revenue"],
                          rows[0]["adr"], rows[0]["revpar"]), tuple(map(Decimal, ("2.5", "25", "100", "40", "10"))))
        self.assertEqual(rows[1]["room_revenue"], 0)
        self.assertEqual(rows[0]["pms_rate_room_type_id"], "")
        self.assertEqual(rows[0]["business_date"], self.days[0])
        self.assertNotIn("总计", {row["room_type_name"] for row in rows})

    def test_two_single_day_requests_use_pms_id_and_are_spaced(self):
        session, wait = MagicMock(), MagicMock()
        with patch.object(report, "request_json", side_effect=[self.payload(day) for day in self.days]) as request:
            rows = report.collect("cookie", "hotel", "测试酒店", "123", session=session, clock=lambda: self.now, wait=wait)
        self.assertEqual(len(rows), 4)
        self.assertEqual(request.call_count, 2)
        wait.assert_called_once_with(1)
        for day, call in zip(self.days, request.call_args_list):
            self.assertEqual(call.args, (session, "POST", report.REPORT_URL))
            self.assertEqual(call.kwargs["json"], {"hotelId": "123", "startDate": str(day), "endDate": str(day), "searchCategory": []})

    def test_wrong_date_org_total_and_empty_summary_fail_before_write(self):
        for kind in ("date", "org", "total", "empty"):
            payload = self.payload(self.days[0])
            if kind == "date":
                payload["data"]["variables"]["endDate"] = "2026-10-26"
            elif kind == "org":
                payload["data"]["variables"]["hotelId"] = "999"
            elif kind == "total":
                payload["data"]["data"]["summaryList"][-1]["roomFee"] = "101"
            else:
                payload["data"]["data"]["summaryList"] = []
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "JL01"):
                report.transform(payload, "123", self.days[0], self.now, "hotel", "测试酒店")

    def test_second_request_failure_no_retry_or_partial_commit(self):
        with patch.object(report, "request_json", side_effect=[self.payload(self.days[0]), RuntimeError("failed")]) as request, \
                patch.object(report, "sync_mysql") as sync, self.assertRaises(RuntimeError):
            report.collect("cookie", "hotel", "测试酒店", "123", session=MagicMock(), clock=lambda: self.now, wait=MagicMock())
        self.assertEqual(request.call_count, 2)
        sync.assert_not_called()

    def test_last_year_legal_empty_is_not_zero_and_preserves_existing_history(self):
        empty = self.payload(self.days[1])
        empty["data"]["data"]["summaryList"] = []
        with patch.object(report, "request_json", side_effect=[self.payload(self.days[0]), empty]):
            rows = report.collect("cookie", "hotel", "测试酒店", "123", session=MagicMock(),
                                  clock=lambda: self.now, wait=MagicMock())
        self.assertEqual({row["business_date"] for row in rows}, {self.days[0]})
        db = self.database()
        db.seed(dict(self.batch()[0], business_date=self.days[1], room_revenue=Decimal("77")))
        report.sync_mysql(rows, db)
        saved = db.db.execute(f"SELECT room_revenue FROM {report.TABLE} WHERE business_date='2025-10-27'").fetchall()
        self.assertEqual(saved, [("77",)])
        empty["data"]["variables"]["startDate"] = "2025-10-26"
        with self.assertRaises(ValueError):
            report.transform(empty, "123", self.days[1], self.now, "hotel", "测试酒店")

    def test_cross_midnight_does_not_continue(self):
        clock = iter([self.now, self.now + timedelta(days=1)])
        with patch.object(report, "request_json", return_value=self.payload(self.days[0])) as request, self.assertRaises(ValueError):
            report.collect("cookie", "hotel", "测试酒店", "123", session=MagicMock(), clock=lambda: next(clock), wait=MagicMock())
        request.assert_called_once()

    def test_upsert_updates_target_keeps_history_other_hotels_and_maps_exact_aliases(self):
        db = self.database()
        row = self.batch()[0]
        db.seed(dict(row, room_revenue=Decimal("1")))
        db.seed(dict(row, business_date=date(2026, 10, 25), source_platform="其他PMS"))
        db.seed(dict(row, hotel_id="other"))
        db.db.executemany("INSERT INTO hotel_room_type_mapping VALUES ('hotel','pms_meituan',1,'',?,?)",
                          [("测试房型", "ROOM-1"), ("其他别名", "ROOM-1"), ("零值房型", "ROOM-2"), ("零值房型", "ROOM-3")])
        db.commit()
        report.sync_mysql(self.batch(), db)
        report.sync_mysql(self.batch(), db)
        self.assertEqual(len(db.contents()), 6)
        rows = db.db.execute(f"SELECT room_type_name,room_revenue,room_type_id FROM {report.TABLE} "
                             "WHERE hotel_id='hotel' AND business_date='2026-10-27'").fetchall()
        self.assertEqual({name: (revenue, room_id) for name, revenue, room_id in rows},
                         {"测试房型": ("100", "ROOM-1"), "零值房型": ("0", None)})

    def test_other_pms_or_newer_snapshot_blocks_transaction(self):
        for newer in (False, True):
            db = self.database()
            row = dict(self.batch()[0], source_platform=report.SOURCE if newer else "其他PMS")
            if newer:
                row["snapshot_time"] += timedelta(seconds=1)
            db.seed(row)
            before = db.contents()
            with self.assertRaises(ValueError):
                report.sync_mysql(self.batch(), db)
            self.assertEqual(db.contents(), before)

    def test_four_hundred_day_boundary_keeps_last_year_and_scopes_cleanup(self):
        for snapshot, cutoff in ((self.now, date(2025, 9, 24)),
                                 (datetime(2027, 1, 1, 12), date(2025, 11, 28)),
                                 (datetime(2024, 3, 1, 12), date(2023, 1, 27))):
            with self.subTest(snapshot=snapshot):
                self.now = snapshot
                self.days = report.collection_dates(snapshot.date())
                db = self.database()
                row = self.batch()[0]
                expired = cutoff - timedelta(days=1)
                db.seed(dict(row, business_date=expired))
                db.seed(dict(row, business_date=cutoff))
                db.seed(dict(row, business_date=expired, source_platform="其他PMS", room_type_name="其他来源"))
                db.seed(dict(row, business_date=expired, hotel_id="other"))
                report.sync_mysql(self.batch(), db)
                self.assertEqual((snapshot.date() - cutoff).days + 1, 400)
                saved = db.db.execute(f"SELECT business_date FROM {report.TABLE} "
                                      "WHERE hotel_id='hotel' AND source_platform=?", (report.SOURCE,)).fetchall()
                dates = {date.fromisoformat(day) for day, in saved}
                self.assertNotIn(expired, dates)
                self.assertIn(cutoff, dates)
                self.assertIn(self.days[1], dates)
                self.assertEqual(len(db.contents()), 7)

    def test_cleanup_failure_rolls_back_updates_and_keeps_expired_rows(self):
        db = self.database()
        row = self.batch()[0]
        db.seed(dict(row, room_revenue=Decimal("1")))
        db.seed(dict(row, business_date=date(2025, 9, 23)))
        before = db.contents()
        db.db.execute(f"CREATE TRIGGER reject_cleanup BEFORE DELETE ON {report.TABLE} "
                      "BEGIN SELECT RAISE(ABORT, 'cleanup failed'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            report.sync_mysql(self.batch(), db)
        self.assertEqual(db.contents(), before)

    def test_insert_failure_rolls_back_both_days(self):
        db = self.database()
        db.seed(dict(self.batch()[0], room_revenue=Decimal("1")))
        before = db.contents()
        db.db.execute(f"CREATE TRIGGER reject_old_day BEFORE INSERT ON {report.TABLE} "
                      "WHEN NEW.business_date='2025-10-27' BEGIN SELECT RAISE(ABORT, 'failed'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            report.sync_mysql(self.batch(), db)
        self.assertEqual(db.contents(), before)

    def test_missing_day_duplicates_total_and_wrong_source_never_connect(self):
        rows = self.batch()
        for batch in ([], self.rows(self.days[1]), rows + [rows[0]],
                      [dict(row, source_platform="wrong") for row in rows],
                      [dict(row, room_type_name="总计") for row in rows]):
            connection = MagicMock()
            with self.assertRaises(ValueError):
                report.sync_mysql(batch, connection)
            connection.cursor.assert_not_called()

    def test_dry_run_failed_collect_and_portable_entry(self):
        with patch.object(report, "collect", return_value=self.batch()), patch.object(report, "sync_mysql") as sync:
            self.assertEqual(report.main(["--dry-run"]), 0)
        sync.assert_not_called()
        with patch.object(report, "collect", side_effect=ValueError("invalid")), patch.object(report, "sync_mysql") as sync:
            self.assertEqual(report.main([]), 1)
        sync.assert_not_called()
        result = subprocess.run([sys.executable, str(ROOT / "meituan_pms/room_performance_report.py"), "--help"],
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
