"""美团 PMS 最近 30 天总营业指标的酒店、日期和写入边界。"""
import copy
import os
import subprocess
import sys
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "meituan_pms"), str(ROOT / "OTA采集服务")]
import daily_report as report
import runner


class MeituanPmsDailyReportTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 27, 20, 50)
        self.day = date(2026, 10, 26)
        self.summary = {"orgId": 123, "businessDate": "2026-10-26", "roomCount": 110,
                        "nightRoomCount": 11, "maintainRoomCount": 0, "roomRent": 1569.80}
        self.payload = {"code": 10000, "data": {"variables": {"startDate": "2026-10-26", "endDate": "2026-10-26"},
                        "data": {"summaryDetailList": [self.summary], "customerCategoryList": [{"bad": "ignored"}]}}}

    def row(self):
        return report.transform(self.payload, "123", [self.day], self.now, "hotel", "酒店")[0]

    def range_payload(self):
        dates = report.collection_dates(self.now.date())
        payload = copy.deepcopy(self.payload)
        payload["data"]["variables"].update(startDate=dates[0].isoformat(), endDate=dates[-1].isoformat())
        payload["data"]["data"]["summaryDetailList"] = [dict(self.summary, businessDate=day.isoformat()) for day in dates]
        return dates, payload

    def rows(self):
        dates, payload = self.range_payload()
        return report.transform(payload, "123", dates, self.now, "hotel", "酒店")

    def test_sample_totals_map_to_daily_table_with_no_classification(self):
        row = self.row()
        self.assertEqual((row["dimension_type"], row["dimension_name"]), ("总营业指标", "总营业指标"))
        self.assertEqual((row["room_count"], row["room_nights"]), (110, 11))
        self.assertEqual(row["occupancy_rate"], Decimal("10.00"))
        self.assertEqual(row["adr"], Decimal("142.71"))
        self.assertEqual(row["revpar"], Decimal("14.27"))
        self.assertIsNone(row["orders_today"])
        self.assertEqual(row["business_date"], self.day)

    def test_zero_denominators_match_page_zero(self):
        self.summary.update(roomCount=0, nightRoomCount=0, roomRent=0)
        row = self.row()
        for field in ("occupancy_rate", "adr", "revpar"):
            self.assertEqual(row[field], Decimal("0.00"))

    def test_rejects_wrong_hotel_day_range_empty_or_duplicate(self):
        mutations = [lambda: self.summary.update(orgId=999),
                     lambda: self.summary.update(businessDate="2026-10-25"),
                     lambda: self.payload["data"]["variables"].update(endDate="2026-10-27"),
                     lambda: self.payload["data"]["data"]["summaryDetailList"].clear(),
                     lambda: self.payload["data"]["data"]["summaryDetailList"].append(copy.deepcopy(self.summary))]
        for mutate in mutations:
            self.setUp()
            mutate()
            with self.assertRaises(ValueError):
                self.row()

    def test_rejects_missing_or_invalid_metrics(self):
        for field, value in (("roomRent", "NaN"), ("roomRent", "invalid"),
                             ("nightRoomCount", "1.5"), ("roomCount", -1), ("maintainRoomCount", 111)):
            self.setUp()
            self.summary[field] = value
            with self.assertRaises(ValueError):
                self.row()

    def test_explicit_historical_nulls_match_page_zero_but_missing_fields_fail(self):
        self.summary.update(nightRoomCount=None, roomRent=None)
        row = self.row()
        self.assertEqual(row["room_count"], 110)
        for field in ("room_nights", "room_revenue", "occupancy_rate", "adr", "revpar", "sold_rooms"):
            self.assertEqual(row[field], 0)
        self.assertEqual(row["remaining_rooms"], 110)
        self.assertIsNone(row["orders_today"])
        self.summary.pop("roomRent")
        with self.assertRaises(ValueError):
            self.row()

    def test_explicit_period_total_is_not_duplicated(self):
        self.payload["data"]["data"]["summaryDetailList"].append({"businessDate": "", "businessDateWeek": "总计"})
        self.assertEqual(self.row()["room_nights"], 11)

    def test_single_request_covers_thirty_closed_days_including_year_boundary(self):
        session = MagicMock()
        dates, payload = self.range_payload()
        with patch.object(report, "request_json", return_value=payload) as request:
            rows = report.collect("cookie", "hotel", "酒店", "123", session=session, clock=lambda: self.now)
        self.assertEqual(len(rows), 30)
        self.assertEqual([row["business_date"] for row in rows], dates)
        request.assert_called_once()
        self.assertEqual(request.call_args.args[1:3], ("POST", report.REPORT_URL))
        body = request.call_args.kwargs["json"]
        self.assertEqual((body["startDate"], body["endDate"], body["orgId"], body["orgIds"]),
                         ("2026-09-27", "2026-10-26", "123", ["123"]))
        with patch.object(report, "request_json", side_effect=RuntimeError("stop")) as request:
            with self.assertRaises(RuntimeError):
                report.collect("cookie", "hotel", "酒店", "123", session=session, clock=lambda: datetime(2027, 1, 1))
        self.assertEqual(request.call_args.kwargs["json"]["startDate"], "2026-12-02")
        self.assertEqual(request.call_args.kwargs["json"]["endDate"], "2026-12-31")

    def test_missing_or_duplicate_day_rejects_the_whole_window(self):
        for duplicate in (False, True):
            dates, payload = self.range_payload()
            items = payload["data"]["data"]["summaryDetailList"]
            if duplicate:
                items.append(copy.deepcopy(items[0]))
            else:
                items.pop(12)
            with self.assertRaises(ValueError):
                report.transform(payload, "123", dates, self.now, "hotel", "酒店")

    def test_missing_org_id_stops_before_network_and_env_is_independent(self):
        session = MagicMock()
        with self.assertRaises(ValueError):
            report.collect("cookie", "hotel", "酒店", "", session=session)
        session.request.assert_not_called()
        env = runner.build_env({"meituan_pms": {"org_id": "123"}}, "meituan_pms")
        self.assertEqual(env["MEITUAN_PMS_ORG_ID"], "123")

    def test_upsert_is_only_daily_total_and_cleans_expired_history(self):
        connection = MagicMock()
        cur = connection.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [("hotel",)]
        report.sync_mysql(self.rows(), connection)
        self.assertEqual(cur.execute.call_count, 2)
        sql, rows = cur.executemany.call_args.args
        self.assertIn("INSERT INTO `jy01_hotel_statistics_daily`", sql)
        self.assertNotIn("jy03_hotel_statistics_month", sql)
        self.assertNotIn("DELETE", sql)
        self.assertIn("room_revenue=COALESCE(VALUES(room_revenue), room_revenue)", sql)
        self.assertEqual(len(rows), 30)
        self.assertTrue(all(row["dimension_name"] == "总营业指标" for row in rows))
        connection.commit.assert_called_once()

    def test_page_zero_is_passed_to_upsert_instead_of_preserving_old_metric(self):
        self.summary.update(nightRoomCount=None, roomRent=None)
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [("hotel",)]
        report.sync_mysql(self.rows(), connection)
        sql, rows = cursor.executemany.call_args.args
        self.assertIn("room_revenue=COALESCE(VALUES(room_revenue), room_revenue)", sql)
        self.assertTrue(all(row["room_revenue"] == 0 and row["room_nights"] == 0 for row in rows))

    def test_wrong_hotel_or_failed_insert_rolls_back(self):
        for collision in (True, False):
            connection = MagicMock()
            cur = connection.cursor.return_value.__enter__.return_value
            cur.fetchall.return_value = [("other-hotel",)] if collision else []
            if not collision:
                cur.executemany.side_effect = RuntimeError("insert failed")
            with self.assertRaises((ValueError, RuntimeError)):
                report.sync_mysql(self.rows(), connection)
            connection.commit.assert_not_called()
            connection.rollback.assert_called_once()

    def test_dry_run_and_script_entry(self):
        with patch.dict(os.environ, {"MEITUAN_PMS_ORG_ID": "123"}), \
             patch.object(report, "collect", return_value=self.rows()), patch.object(report, "sync_mysql") as sync:
            self.assertEqual(report.main(["--dry-run"]), 0)
        sync.assert_not_called()
        result = subprocess.run([sys.executable, str(ROOT / "meituan_pms/daily_report.py"), "--help"],
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
