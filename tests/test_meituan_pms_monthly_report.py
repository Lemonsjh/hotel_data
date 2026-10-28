"""美团 PMS 月报范围、总营业指标和事务边界；不访问真实后台或数据库。"""
import copy
import subprocess
import sys
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "meituan_pms"), str(ROOT / "OTA采集服务")]
import monthly_report as report


class MeituanPmsMonthlyReportTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 27, 21, 14)
        self.item = {"orgId": 123, "businessDateMonth": "2026-10", "roomCount": 2860,
                     "nightRoomCount": 122, "maintainRoomCount": 0, "roomRent": 21450.99,
                     "otherAmount": 339, "startDate": "2026-10-01", "endDate": "2026-10-31"}
        self.payload = {"code": 10000, "data": {"data": {"monthSummaryDetailList": [self.item],
                        "monthCustomerCategoryList": [{"ignored": True}]}}}

    def rows(self, *, initial=True, year=2026):
        return report.transform(self.payload, "123", year, self.now, "hotel", "酒店", initial=initial)

    def test_monthly_room_count_is_already_cumulative_and_no_classifications(self):
        row = self.rows()[0]
        self.assertEqual(row["room_count"], 2860)
        self.assertEqual(row["room_nights"], 122)
        self.assertEqual(row["room_revenue"], Decimal("21450.99"))
        self.assertEqual(row["adr"], Decimal("175.83"))
        self.assertEqual(row["revpar"], Decimal("7.50"))
        self.assertEqual(row["occupancy_rate"], Decimal("4.27"))
        self.assertEqual(row["dimension_name"], "总营业指标")

    def test_initial_returns_existing_months_and_incremental_returns_only_current(self):
        self.payload["data"]["data"]["monthSummaryDetailList"].append(dict(self.item, businessDateMonth="2026-08"))
        self.assertEqual([row["period_month"] for row in self.rows()], ["2026-08", "2026-10"])
        self.assertEqual([row["period_month"] for row in self.rows(initial=False)], ["2026-10"])

    def test_wrong_org_year_or_duplicate_month_is_rejected(self):
        for field, value in (("orgId", 999), ("businessDateMonth", "2025-10"), ("businessDateMonth", "2026-13"),
                             ("roomRent", None), ("nightRoomCount", "bad")):
            self.setUp()
            self.item[field] = value
            with self.assertRaises(ValueError):
                self.rows()
        self.setUp()
        self.payload["data"]["data"]["monthSummaryDetailList"].append(copy.deepcopy(self.item))
        with self.assertRaises(ValueError):
            self.rows()

    def test_empty_prior_year_is_legal_and_does_not_create_zero_months(self):
        self.payload["data"]["data"]["monthSummaryDetailList"] = []
        self.assertEqual(self.rows(year=2025), [])

    def test_collect_initial_queries_both_years_and_incremental_only_current_year(self):
        for initial, years in ((True, [2026, 2025]), (False, [2026])):
            session = MagicMock()
            replies = [self.payload, {"code": 10000, "data": {"data": {"monthSummaryDetailList": []}}}]
            with patch.object(report, "request_json", side_effect=replies) as request:
                rows = report.collect("cookie", "hotel", "酒店", "123", initial=initial,
                                      snapshot=self.now, session=session)
            self.assertEqual([call.kwargs["json"]["year"] for call in request.call_args_list], years)
            self.assertTrue(all(call.kwargs["json"]["searchCategory"] == ["Summary"] for call in request.call_args_list))
            self.assertEqual(len(rows), 1)

    def test_empty_current_month_rejects_incremental_collection(self):
        self.item["businessDateMonth"] = "2026-09"
        with patch.object(report, "request_json", return_value=self.payload), self.assertRaises(ValueError):
            report.collect("cookie", "hotel", "酒店", "123", initial=False, snapshot=self.now, session=MagicMock())

    def test_initial_detection_uses_only_current_hotel_source_and_total_dimension(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        for count, expected in ((0, True), (3, False)):
            cursor.fetchone.return_value = (count,)
            self.assertEqual(report.needs_initial_backfill("hotel", self.now, conn), expected)
            args = cursor.execute.call_args.args[1]
            self.assertEqual(args, ("hotel", "PMS（美团）", "总营业指标", "总营业指标", "2025-01", "2026-12"))

    def test_upsert_only_target_months_without_deleting_history(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [("hotel",)]
        report.sync_mysql(self.rows(), conn)
        sql = cursor.executemany.call_args.args[0]
        self.assertIn("INSERT INTO `jy03_hotel_statistics_month`", sql)
        self.assertNotIn("DELETE", sql)
        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()

    def test_cross_hotel_collision_or_write_failure_rolls_back(self):
        for collision in (True, False):
            conn = MagicMock()
            cursor = conn.cursor.return_value.__enter__.return_value
            cursor.fetchall.return_value = [("other-hotel",)] if collision else []
            if not collision:
                cursor.executemany.side_effect = RuntimeError("write failed")
            with self.assertRaises((ValueError, RuntimeError)):
                report.sync_mysql(self.rows(), conn)
            conn.commit.assert_not_called()
            conn.rollback.assert_called_once()

    def test_dry_run_never_connects_database_and_portable_entry_loads(self):
        with patch.object(report, "collect", return_value=self.rows()) as collect, \
             patch.object(report, "needs_initial_backfill") as detect, patch.object(report, "sync_mysql") as sync:
            self.assertEqual(report.main(["--dry-run"]), 0)
        self.assertTrue(collect.call_args.kwargs["initial"])
        detect.assert_not_called()
        sync.assert_not_called()
        result = subprocess.run([sys.executable, str(ROOT / "meituan_pms/monthly_report.py"), "--help"],
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
