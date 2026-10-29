"""美团 PMS 原生经营业绩日报映射 JL02，离线验证口径及事务边界。"""
import copy
import subprocess
import sys
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "meituan_pms"), str(ROOT / "OTA采集服务")]
import performance_report as report


class MeituanPmsPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 27, 21, 30)
        self.day = date(2026, 10, 26)
        self.items = [{"category": "总营业指标", "statistics": name, "groupName": None,
                       "currentDay": "10.00%" if name == "出租率" else "11.00",
                       "currentMonth": "122.00", "currentYear": "122.00", "lastYear": "999.00"}
                      for name in sorted(report.CORE_METRICS)]
        self.payload = {"code": 10000, "data": {"summaryList": self.items,
                        "dailyIncomeList": [{"category": "门店收入", "statistics": "房费"}]}}

    def rows(self):
        return report.transform(self.payload, "123", self.day, self.now, "hotel", "酒店")

    def batch(self):
        return [{**row, "business_date": day} for day in report.collection_dates(self.now.date()) for row in self.rows()]

    def test_uses_native_three_columns_and_percentage_points(self):
        rows = self.rows()
        rate = next(row for row in rows if row["metric_name"] == "出租率")
        self.assertEqual(rate["value_day"], Decimal("10.00"))
        self.assertEqual(rate["value_month"], Decimal("122.00"))
        self.assertEqual(rate["value_year"], Decimal("122.00"))
        self.assertEqual(len(rows), len(report.CORE_METRICS))

    def test_ignores_room_statistics_and_other_income_and_rejects_duplicates(self):
        self.items.append({"category": "客房统计", "statistics": "客房数"})
        self.assertEqual(len(self.rows()), len(report.CORE_METRICS))
        self.items.append(copy.deepcopy(self.items[0]))
        with self.assertRaises(ValueError):
            self.rows()

    def test_invalid_values_missing_columns_and_core_metrics_are_rejected(self):
        for key, value in (("currentDay", "NaN"), ("currentMonth", "bad"), ("currentYear", True)):
            self.setUp()
            self.items[0][key] = value
            with self.assertRaises(ValueError):
                self.rows()
        self.setUp()
        self.items[0].pop("currentYear")
        with self.assertRaises(ValueError):
            self.rows()
        self.setUp()
        self.items.pop()
        with self.assertRaises(ValueError):
            self.rows()

    def test_native_day_detail_link_is_validated_if_present(self):
        link = "/pms-report/home/report/#CW04?orgId=123&startDate=2026-10-26&endDate=2026-10-26"
        self.items[0]["currentDayUrl"] = link
        self.rows()
        for broken in (link.replace("orgId=123", "orgId=999"), link.replace("2026-10-26", "2026-10-25")):
            self.items[0]["currentDayUrl"] = broken
            with self.assertRaises(ValueError):
                self.rows()

    def test_dates_match_existing_jl02_and_deduplicate_month_first(self):
        self.assertEqual(report.collection_dates(self.now.date()),
                         [date(2026, 10, 26), date(2025, 10, 26), date(2026, 9, 30),
                          date(2025, 9, 30), date(2026, 8, 31), date(2025, 8, 31)])
        self.assertEqual(len(report.collection_dates(date(2026, 10, 1))), 4)
        self.assertEqual(report.same_day_last_year(date(2024, 2, 29)), date(2023, 2, 28))

    def test_six_finite_requests_are_spaced_and_keep_request_body(self):
        wait, session = MagicMock(), MagicMock()
        with patch.object(report, "request_json", return_value=self.payload) as request:
            rows = report.collect("cookie", "hotel", "酒店", "123", clock=lambda: self.now, session=session, wait=wait)
        self.assertEqual(request.call_count, 6)
        self.assertEqual(wait.call_count, 5)
        self.assertEqual(len(rows), 6 * len(report.CORE_METRICS))
        self.assertEqual(request.call_args_list[0].kwargs["json"],
                         {"orgId": "123", "businessDate": "2026-10-26", "statisticsCodes": [],
                          "expandAgentCompany": True, "env": "/pms-report"})

    def test_upsert_writes_only_requested_total_metrics_and_preserves_history(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        report.sync_mysql(self.batch(), connection)
        self.assertIn("INSERT INTO `jl02_hotel_performance_daily`", cursor.executemany.call_args.args[0])
        self.assertTrue(all(row["category"] == "总营业指标" for row in cursor.executemany.call_args.args[1]))
        cleanup = cursor.execute.call_args
        self.assertIn("DELETE FROM `jl02_hotel_performance_daily`", cleanup.args[0])
        self.assertEqual(cleanup.args[1][:2], ("hotel", report.SOURCE))
        connection.commit.assert_called_once()

    def test_other_pms_collision_and_write_failure_roll_back(self):
        for collision in (True, False):
            connection = MagicMock()
            cursor = connection.cursor.return_value.__enter__.return_value
            cursor.fetchall.return_value = [(self.day, "间夜数", "PMS（别样红）")] if collision else []
            if not collision:
                cursor.executemany.side_effect = RuntimeError("write failed")
            with self.assertRaises((ValueError, RuntimeError)):
                report.sync_mysql(self.batch(), connection)
            connection.commit.assert_not_called()
            connection.rollback.assert_called_once()

    def test_incomplete_batch_never_connects_to_write(self):
        with self.assertRaises(ValueError):
            report.sync_mysql(self.rows(), MagicMock())

    def test_dry_run_and_portable_script_entry(self):
        with patch.object(report, "collect", return_value=self.batch()), patch.object(report, "sync_mysql") as sync:
            self.assertEqual(report.main(["--dry-run"]), 0)
        sync.assert_not_called()
        result = subprocess.run([sys.executable, str(ROOT / "meituan_pms/performance_report.py"), "--help"],
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
