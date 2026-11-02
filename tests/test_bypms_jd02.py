"""宝寓离店房晚分页、逐客关联、失败保护及共用60天事务写入。"""
import copy
import sqlite3
import subprocess
import sys
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "宝寓PMS数据采集代码"))
import bypms_jd02_data as report
from tests.test_meituan_pms_guest_report import Database
import collection_schema


class BypmsGuestTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 30, 16)
        self.day = self.now.date() - timedelta(days=1)
        self.night = {"id": 100, "contractId": 200, "roomNo": "205", "roomTypeName": "测试双床房",
                      "checkIn": "2026-10-28 15:00:00", "checkOut": "2026-10-29 14:00:00", "price": "77.29",
                      "state": "E", "channel": "Meituan", "channelName": "美团酒店", "accountName": "渠道账号",
                      "contractType": "F", "infoRemark": "测试备注"}
        self.guest = {"id": 300, "contractId": 200, "roomNightId": 100, "renterName": "测试客人甲",
                      "renterPhone": "138****0000", "renterIdcard": "TEST****1234", "emplName": "测试操作员"}
        self.data = {"night": {"id": 100, "renterCount": 2},
                     "binds": [self.guest, dict(self.guest, id=301, renterName="测试客人乙")]}

    def rows(self):
        return report.transform(self.night, self.data, self.day, self.now, "hotel", "测试酒店")

    def database(self):
        db = Database(self.rows()[0])
        self.addCleanup(db.db.close)
        return db

    def test_individual_guests_keep_native_values_and_unknown_membership_is_not_account(self):
        rows = self.rows()
        self.assertEqual([r["customer_name"] for r in rows], ["测试客人甲", "测试客人乙"])
        self.assertEqual([r["order_id"] for r in rows], ["200", "200"])
        self.assertEqual([r["customer_id"] for r in rows], ["300", "301"])
        self.assertEqual(rows[0]["room_price"], Decimal("77.29"))
        self.assertEqual(rows[0]["arrival_time"], datetime(2026, 10, 28, 15))
        self.assertEqual(rows[0]["checkin_status"], "已退房")
        self.assertIsNone(rows[0]["main_checkin_person"])
        self.assertIsNone(rows[0].get("member_level"))
        self.assertEqual(rows[0]["id_card_number"], "TEST****1234")

    def test_checkout_pagination_uses_date_range_and_spaced_pages(self):
        pages = [{"nightList": [self.night], "total": 2},
                 {"nightList": [dict(self.night, id=101)], "total": 2}]
        wait = MagicMock()
        with patch.object(report, "request_data", side_effect=pages) as request:
            result = report.fetch_nights(MagicMock(), self.day, wait)
        self.assertEqual(len(result), 2)
        self.assertEqual(request.call_args_list[0].args[2],
                         {"type": "checkout", "date": "2026-10-29,2026-10-29", "page": 1, "pageSize": 20})
        self.assertEqual(request.call_args_list[1].args[2]["page"], 2)
        wait.assert_called_once_with(1)

    def test_duplicate_pages_changed_total_and_truncated_results_fail(self):
        first = {"nightList": [self.night], "total": 2}
        for last in ({"nightList": [self.night], "total": 2}, {"nightList": [], "total": 2},
                     {"nightList": [], "total": 3}):
            with patch.object(report, "request_data", side_effect=[first, last]), self.assertRaises(ValueError):
                report.fetch_nights(MagicMock(), self.day, MagicMock())
        with patch.object(report, "request_data", return_value={"nightList": [], "total": 0}):
            self.assertEqual(report.fetch_nights(MagicMock(), self.day), [])

    def test_room_and_contract_mismatch_or_wrong_departure_fail(self):
        original = copy.deepcopy(self.data)
        for change in (lambda: self.data["night"].update(id=999), lambda: self.data.update(binds=None),
                       lambda: self.data["binds"][0].update(roomNightId=999),
                       lambda: self.data["binds"][0].update(contractId=999)):
            self.data = copy.deepcopy(original)
            change()
            with self.assertRaises(ValueError):
                self.rows()
        self.data = original
        self.night["checkOut"] = "2026-10-30 14:00:00"
        with self.assertRaises(ValueError):
            self.rows()

    def test_zero_renter_count_header_does_not_hide_registered_guests(self):
        self.data["night"]["renterCount"] = 0
        self.assertEqual(len(self.rows()), 2)

    def test_collect_queries_all_guests_and_a_failure_never_retries_or_returns_partial_rows(self):
        responses = [{"nightList": [self.night], "total": 1}, self.data]
        with patch.object(report, "request_data", side_effect=responses) as request:
            rows, snapshot = report.collect("cookie", "hotel", "酒店", session=MagicMock(), clock=lambda: self.now)
        self.assertEqual((len(rows), snapshot), (2, self.now))
        self.assertEqual(request.call_args.args[2], {"nightId": 100})
        with patch.object(report, "request_data", side_effect=[responses[0], RuntimeError("failed")]) as request:
            with self.assertRaises(RuntimeError):
                report.collect("cookie", "hotel", "酒店", session=MagicMock(), clock=lambda: self.now)
        self.assertEqual(request.call_count, 2)

    def test_api_authentication_failure_and_malformed_data_fail_without_dumping_response(self):
        session = MagicMock()
        for payload in ({"state": 403, "data": None}, {"state": 0, "data": []}):
            session.get.return_value.json.return_value = payload
            with self.assertRaises(RuntimeError):
                report.request_data(session, report.GUEST_URL, {"nightId": 100})

    def test_shared_write_isolates_sources_and_retention_and_keeps_both_guests(self):
        db, row = self.database(), self.rows()[0]
        cutoff = self.day - timedelta(days=59)
        for saved in (dict(row, business_date=cutoff), dict(row, business_date=cutoff - timedelta(days=1)),
                      dict(row, business_date=cutoff - timedelta(days=1), source_platform="PMS（美团）"),
                      dict(row, business_date=cutoff - timedelta(days=1), hotel_id="other")):
            db.seed(saved)
        db.db.executemany("INSERT INTO hotel_room_type_mapping VALUES ('hotel',?,1,'','测试双床房',?)",
                          [("pms_bypms", "BY-1"), ("pms_meituan", "MT-1")])
        db.commit()
        for _ in range(2):
            report.sync_mysql(self.rows(), "hotel", self.now, db)
        self.assertEqual(len(db.contents()), 5)
        current = db.db.execute(f"SELECT customer_name,room_type_id FROM {report.TABLE} "
                                "WHERE hotel_id='hotel' AND source_platform=? AND business_date=? ORDER BY customer_id",
                                (report.SOURCE, str(self.day))).fetchall()
        self.assertEqual(current, [("测试客人甲", "BY-1"), ("测试客人乙", "BY-1")])

    def test_write_and_cleanup_failures_preserve_previous_snapshot_and_history(self):
        for event in ("INSERT", "DELETE"):
            db, row = self.database(), self.rows()[0]
            db.seed(dict(row, customer_name="原记录"))
            db.seed(dict(row, business_date=self.day - timedelta(days=60)))
            before = db.contents()
            condition = "" if event == "INSERT" else f"WHEN OLD.business_date < '{self.day}'"
            db.db.execute(f"CREATE TRIGGER reject_write BEFORE {event} ON {report.TABLE} {condition} "
                          "BEGIN SELECT RAISE(ABORT, 'failed'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                report.sync_mysql(self.rows(), "hotel", self.now, db)
            self.assertEqual(db.contents(), before)

    def test_empty_guests_are_valid_and_empty_snapshot_cleans_only_own_day(self):
        self.data["binds"].clear()
        self.data["night"]["renterCount"] = 0
        self.assertEqual(self.rows(), [])
        connection = MagicMock()
        report.sync_mysql([], "hotel", self.now, connection)
        cur = connection.cursor.return_value.__enter__.return_value
        self.assertEqual(cur.execute.call_args_list[0].args[1], ("hotel", report.SOURCE, self.day))
        self.assertEqual(cur.execute.call_args.args[1], ("hotel", report.SOURCE, self.day - timedelta(days=59)))
        connection.commit.assert_called_once()

    def test_dry_run_no_database_write_schema_and_portable_entry(self):
        with patch.object(report, "collect", return_value=(self.rows(), self.now)), patch.object(report, "sync_mysql") as sync:
            self.assertEqual(report.main(["--dry-run"]), 0)
        sync.assert_not_called()
        for column in self.rows()[0]:
            self.assertIn(f"`{column}`", collection_schema.table_definitions()[report.TABLE])
        process = subprocess.run([sys.executable, str(ROOT / "宝寓PMS数据采集代码/bypms_jd02_data.py"), "--help"],
                                 capture_output=True, timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
