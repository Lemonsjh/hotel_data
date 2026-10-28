"""JD01 最近 30 天预订中快照、同订单多房型、合法空结果和失败保护。"""
import subprocess
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "meituan_pms"), str(ROOT / "OTA采集服务")]
import booking_report as report


class MeituanPmsBookingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 28, 11)
        self.item = {"orderNo": "TEST-ORDER", "orderDate": "2026-10-14", "contractName": "测试联系人",
                     "source": "中介", "customerLevel": "测试渠道", "estArriveTime": "2026-10-28 15:00:00",
                     "estDepatureTime": "2026-10-29 14:00:00", "roomType": "测试大床房", "roomCount": 1,
                     "roomPrice": 160, "roomPriceType": "自定义价", "prePayAmount": None,
                     "expireKeepTime": None, "orderStatus": "预订中", "remark": None,
                     "operator": "测试操作员", "prePaymentType": "无担保"}
        self.items = [self.item, dict(self.item, roomType="测试双床房", roomCount=4)]
        self.payload = {"code": 10000, "data": {"variables": {"orgId": "123", "startDate": "2026-09-29",
                        "endDate": "2026-10-28", "dateType": None}, "dataList": self.items}}

    def rows(self):
        return report.transform(self.payload, "123", self.now.date(), self.now, "hotel", "测试酒店")

    def test_same_order_multitype_rows_are_preserved_and_booking_date_is_not_arrival(self):
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual([row["order_id"] for row in rows], ["TEST-ORDER", "TEST-ORDER"])
        self.assertEqual([row["room_count"] for row in rows], [1, 4])
        self.assertEqual(rows[0]["booking_time"], datetime(2026, 10, 14))
        self.assertIsNone(rows[0]["prepayment"])

    def test_request_uses_thirty_day_window_and_keeps_progress_filter(self):
        body = report.request_body("123", self.now.date())
        self.assertEqual((body["dateType"], body["startDate"], body["endDate"], body["orderStatus"]),
                         ("1", "2026-09-29", "2026-10-28", "InProgress"))
        with patch.object(report, "request_json", return_value=self.payload) as request:
            rows, snapshot = report.collect("cookie", "hotel", "测试酒店", "123", session=MagicMock(), clock=lambda: self.now)
        request.assert_called_once()
        self.assertEqual(len(rows), 2)
        self.assertEqual(snapshot, self.now)

    def test_window_boundaries_include_first_day_and_today_but_not_outside_dates(self):
        for arrival, valid in (("2026-09-29 00:00:00", True), ("2026-10-28 23:59:59", True),
                               ("2026-09-28 23:59:59", False), ("2026-10-29 00:00:00", False)):
            with self.subTest(arrival=arrival):
                self.item["estArriveTime"] = arrival
                if valid:
                    self.rows()
                else:
                    with self.assertRaises(ValueError):
                        self.rows()
        for day in (datetime(2027, 1, 1).date(), datetime(2024, 3, 1).date()):
            start, end = report.collection_window(day)
            self.assertEqual((end - start).days + 1, 30)
            self.assertEqual(end, day)

    def test_wrong_hotel_date_status_and_incomplete_response_fail(self):
        changes = [lambda: self.payload["data"]["variables"].update(orgId="999"),
                   lambda: self.payload["data"]["variables"].update(startDate="2026-10-27"),
                   lambda: self.item.update(estArriveTime="2026-10-29 15:00:00"),
                   lambda: self.item.update(orderStatus="已取消"),
                   lambda: self.item.update(roomCount=0),
                   lambda: self.payload["data"].update(total=3),
                   lambda: self.payload["data"].pop("dataList")]
        for change in changes:
            self.setUp()
            change()
            with self.assertRaises(ValueError):
                self.rows()

    def test_legal_empty_results_require_valid_header(self):
        self.items.clear()
        self.assertEqual(self.rows(), [])
        self.payload["data"]["variables"] = {}
        with self.assertRaises(ValueError):
            self.rows()

    def test_unassigned_room_type_is_preserved_without_guessing(self):
        self.item["roomType"] = None
        self.assertIsNone(self.rows()[0]["room_type_name"])

    def test_midnight_crossing_stops_snapshot(self):
        clock = iter([self.now, self.now + timedelta(days=1)])
        with patch.object(report, "request_json", return_value=self.payload), self.assertRaises(ValueError):
            report.collect("cookie", "hotel", "测试酒店", "123", session=MagicMock(), clock=lambda: next(clock))

    def test_snapshot_scopes_delete_and_maps_exact_pms_aliases(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [("测试大床房", "ROOM-1"), ("测试双床房", "ROOM-2"), ("测试双床房", "ROOM-3")]
        report.sync_mysql(self.rows(), "hotel", self.now, connection)
        sql, params = cursor.execute.call_args_list[1].args
        self.assertIn("source_platform=%s", sql)
        self.assertIn("booking_status IN (%s, %s)", sql)
        self.assertEqual(params[:2], ("hotel", "PMS（美团）"))
        self.assertEqual(params[2:4], (datetime(2026, 9, 29), datetime(2026, 10, 29)))
        rows = cursor.executemany.call_args.args[1]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["room_type_id"], "ROOM-1")
        self.assertIsNone(rows[1]["room_type_id"])
        connection.commit.assert_called_once()

    def test_empty_snapshot_refreshes_thirty_days_and_cleans_expired_history_without_inserts(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        report.sync_mysql([], "hotel", self.now, connection)
        self.assertEqual(cursor.execute.call_count, 2)
        refresh_sql, refresh_params = cursor.execute.call_args_list[0].args
        self.assertIn("DELETE FROM `jd01_booking_detail`", refresh_sql)
        self.assertEqual(refresh_params[2:4], (datetime(2026, 9, 29), datetime(2026, 10, 29)))
        self.assertEqual(cursor.execute.call_args.args[1], ("hotel", report.SOURCE, datetime(2026, 7, 31)))
        cursor.executemany.assert_not_called()
        connection.commit.assert_called_once()

    def test_retention_keeps_ninety_arrival_dates_including_today_and_scopes_source(self):
        for snapshot, cutoff in ((self.now, datetime(2026, 7, 31)),
                                 (datetime(2027, 1, 1, 3), datetime(2026, 10, 4)),
                                 (datetime(2024, 3, 1, 3), datetime(2023, 12, 3))):
            with self.subTest(snapshot=snapshot):
                connection = MagicMock()
                cursor = connection.cursor.return_value.__enter__.return_value
                report.sync_mysql([], "hotel", snapshot, connection)
                sql, params = cursor.execute.call_args.args
                self.assertEqual(sql, "DELETE FROM `jd01_booking_detail` WHERE hotel_id=%s "
                                 "AND source_platform=%s AND arrival_time < %s")
                self.assertEqual(params, ("hotel", "PMS（美团）", cutoff))
                self.assertEqual((snapshot.date() - cutoff.date()).days + 1, 90)

    def test_retention_failure_rolls_back_insert_and_snapshot_refresh(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        cursor.execute.side_effect = [None, None, RuntimeError("cleanup failed")]
        with self.assertRaises(RuntimeError):
            report.sync_mysql(self.rows(), "hotel", self.now, connection)
        cursor.executemany.assert_called_once()
        connection.commit.assert_not_called()
        connection.rollback.assert_called_once()

    def test_write_failure_rolls_back_snapshot(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        cursor.executemany.side_effect = RuntimeError("insert failed")
        with self.assertRaises(RuntimeError):
            report.sync_mysql(self.rows(), "hotel", self.now, connection)
        connection.commit.assert_not_called()
        connection.rollback.assert_called_once()

    def test_dry_run_never_writes_and_portable_entry_loads(self):
        with patch.object(report, "collect", return_value=(self.rows(), self.now)), patch.object(report, "sync_mysql") as sync:
            self.assertEqual(report.main(["--dry-run"]), 0)
        sync.assert_not_called()
        result = subprocess.run([sys.executable, str(ROOT / "meituan_pms/booking_report.py"), "--help"],
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
