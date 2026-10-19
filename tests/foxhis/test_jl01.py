"""JL01 日期、历史房型、原生控制总额和日期范围事务测试。"""
import sqlite3
import sys
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "OTA采集服务/foxhis_pms"))
import foxhis_jl01_data as jl
from foxhis_common import Config, FoxhisError, SOURCE_PLATFORM
from .test_realtime_forecast import DatabaseAdapter


class JL01Tests(unittest.TestCase):
    def setUp(self):
        self.stamp = datetime(2026, 10, 19, 15)
        self.days = jl.collection_dates(self.stamp.date())
        self.config = Config(hotel_id="TEST", hotel_name="测试酒店")
        self.records = [self.record("20261018", "AR", "大床房", 32, 30, 16, "1896.80"),
                        self.record("20261018", "SR", "双床房", 29, 25, 25, "2706.82"),
                        self.record("20261018", "{{{", "总计", 61, 55, 41, "4603.62", "{"),
                        self.record("20261018", "ZZZ", "手工输入", 0, 0, 0, "0", "{"),
                        self.record("20251018", "RR", "去年历史房型", 20, 19, 19, "2353.23"),
                        self.record("20251018", "{{{", "总计", 20, 19, 19, "2353.23", "{")]
        self.controls = self.control("20261018", (61, 55, 41, "74.55", "4603.62", "112.28", "75.47")) + self.control("20251018", (20, 19, 19, "100", "2353.23", "123.85", "117.66"))
        self.db = DatabaseAdapter()
        columns = ",".join(f"`{name}` TEXT" for name in jl.COLUMNS)
        self.db.db.execute(f"CREATE TABLE jl01_room_type_performance_daily ({columns}, UNIQUE(hotel_id,business_date,room_type_name))")
        self.db.db.commit()
        self.connection_patch = patch.object(jl, "mysql_connection", return_value=self.db)
        self.connection_patch.start()

    def tearDown(self):
        self.connection_patch.stop(); self.db.db.close()

    def record(self, day, code, name, total, available, nights, revenue, hall="A"):
        return {"business_date": day, "gkey": "t", "hall": hall, "room_type_code": code, "room_type_name": name,
                "total_rooms": str(total), "available_rooms": str(available), "room_nights": str(nights), "room_revenue": str(revenue)}

    def control(self, day, values):
        return [{"business_date": day, "metric_code": code, "metric_value": str(value)} for code, value in zip(jl.CONTROL_FIELDS, values)]

    def build(self, records=None, controls=None):
        return jl.build_rows(self.records if records is None else records, self.controls if controls is None else controls, self.config, self.days, self.stamp)

    def seed(self, row):
        sql = "INSERT INTO jl01_room_type_performance_daily (" + ",".join(jl.COLUMNS) + ") VALUES (" + ",".join(["%s"] * len(jl.COLUMNS)) + ")"
        self.db.executemany(sql, [tuple(row[c] for c in jl.COLUMNS)]); self.db.commit()

    def contents(self):
        return self.db.db.execute("SELECT hotel_id,source_platform,business_date,room_type_name,room_revenue FROM jl01_room_type_performance_daily ORDER BY hotel_id,business_date,room_type_name").fetchall()

    def test_yesterday_same_calendar_day_last_year_and_leap_day(self):
        self.assertEqual(jl.collection_dates(date(2024, 3, 1)), [date(2024, 2, 29), date(2023, 2, 28)])
        self.assertEqual(jl.collection_dates(date(2026, 1, 1)), [date(2025, 12, 31), date(2024, 12, 31)])

    def test_native_formulas_and_historical_type_preserved(self):
        rows, summary = self.build()
        self.assertEqual(len(rows), 3)
        ar = next(r for r in rows if r["pms_rate_room_type_id"] == "AR")
        self.assertEqual(ar["room_nights"], Decimal(16))
        self.assertEqual(ar["occupancy_rate"], Decimal("53.3333"))
        self.assertEqual(ar["adr"], Decimal("118.55"))
        self.assertEqual(ar["revpar"], Decimal("59.28"))
        prior = next(r for r in rows if r["business_date"].year == 2025)
        self.assertEqual(prior["room_type_name"], "去年历史房型")
        self.assertEqual(prior["pms_rate_room_type_id"], "RR")
        self.assertEqual(summary["manual_zero_rows_skipped"], 1)

    def test_free_rooms_are_excluded_by_query_and_match_paid_nights(self):
        self.assertIn("soldf+soldg+soldc+soldl-ent", jl.JL01_REPORT_QUERY)
        records = [self.record("20261018", "A", "大床房", 6, 6, 4, "400"),
                   self.record("20261018", "{{{", "总计", 6, 6, 4, "400", "{")]
        controls = self.control("20261018", (6, 6, 4, "66.67", "400", "100", "66.67"))
        rows, _ = self.build(records, controls)
        self.assertEqual(rows[0]["room_nights"], Decimal(4))
        self.assertEqual(rows[0]["adr"], Decimal("100.00"))

    def test_hotel_daily_revenue_or_nights_mismatch_fails(self):
        for code in ("013000", "011000", "012000", "014000", "018560"):
            controls = [{**r, "metric_value": "999"} if r["business_date"] == "20261018" and r["metric_code"] == code else r for r in self.controls]
            with self.assertRaises(FoxhisError): self.build(controls=controls)

    def test_detail_and_total_mismatch_fails(self):
        records = [{**r, "room_revenue": "2000"} if r["room_type_code"] == "AR" else r for r in self.records]
        with self.assertRaises(FoxhisError): self.build(records=records)

    def test_missing_prior_year_is_not_fabricated_and_yesterday_required(self):
        records = [r for r in self.records if r["business_date"] == "20261018"]
        controls = [r for r in self.controls if r["business_date"] == "20261018"]
        rows, summary = self.build(records, controls)
        self.assertEqual(len(rows), 2)
        self.assertEqual(summary["missing_dates"], ["2025-10-18"])
        with self.assertRaises(FoxhisError):
            self.build([r for r in self.records if r["business_date"] == "20251018"], self.controls)

    def test_nonzero_unclassified_manual_row_fails(self):
        records = [{**r, "room_revenue": "1"} if r["room_type_code"] == "ZZZ" else r for r in self.records]
        with self.assertRaises(FoxhisError): self.build(records=records)

    def test_duplicate_dimensional_rows_and_bad_values_fail(self):
        with self.assertRaises(FoxhisError): self.build(records=[*self.records, self.records[0]])
        for field, value in (("room_nights", "-1"), ("room_revenue", "NaN"), ("total_rooms", ""), ("available_rooms", "99"), ("gkey", "h")):
            records = [{**r, field: value} if r["room_type_code"] == "AR" else r for r in self.records]
            with self.assertRaises(FoxhisError): self.build(records=records)

    def test_missing_native_control_is_not_ignored(self):
        with self.assertRaises(FoxhisError): self.build(controls=self.controls[1:])

    def test_same_code_across_halls_aggregates_once(self):
        ar = self.records[0]
        records = [{**ar, "total_rooms": "16", "available_rooms": "15", "room_nights": "8", "room_revenue": "948.40"},
                   {**ar, "hall": "B", "total_rooms": "16", "available_rooms": "15", "room_nights": "8", "room_revenue": "948.40"}, *self.records[1:]]
        rows, _ = self.build(records=records)
        self.assertEqual(next(r for r in rows if r["pms_rate_room_type_id"] == "AR")["room_revenue"], Decimal("1896.80"))

    def test_transaction_replaces_only_requested_dates_and_source(self):
        rows, _ = self.build(); row = rows[-1]
        self.seed({**row, "room_type_name": "STALE"})
        self.seed({**row, "business_date": date(2026, 10, 17), "room_type_name": "OTHER_DATE"})
        self.seed({**row, "hotel_id": "OTHER_HOTEL"})
        self.seed({**row, "source_platform": "PMS（宝寓）", "room_type_name": "OTHER_SOURCE"})
        jl.sync_rows(rows, self.config); jl.sync_rows(rows, self.config)
        result = self.contents()
        self.assertEqual(len(result), 6)
        self.assertNotIn("STALE", {r[3] for r in result})
        self.assertIn("OTHER_DATE", {r[3] for r in result})

    def test_foreign_source_conflict_rolls_back(self):
        rows, _ = self.build()
        self.seed({**rows[0], "room_type_name": "STALE"})
        self.seed({**rows[0], "source_platform": "PMS（宝寓）"})
        before = self.contents()
        with self.assertRaises(sqlite3.IntegrityError): jl.sync_rows(rows, self.config)
        self.assertEqual(self.contents(), before)

    def test_empty_mixed_duplicate_and_older_batches_preserve_data(self):
        rows, _ = self.build(); self.seed({**rows[0], "snapshot_time": self.stamp + timedelta(seconds=1)})
        before = self.contents()
        for invalid in ([], [rows[0], rows[0]], [{**rows[0], "business_date": date(2026, 10, 17)}], rows):
            with self.assertRaises(FoxhisError): jl.sync_rows(invalid, self.config)
        self.assertEqual(self.contents(), before)

    def test_dry_run_never_writes(self):
        rows, summary = self.build()
        with patch.object(jl, "fetch_rows", return_value=(rows, summary)), patch.object(jl, "sync_rows", side_effect=AssertionError("不应写入")), patch.object(jl, "save_output"), patch.object(sys, "argv", ["jl01", "--dry-run"]):
            result = jl.collect(self.config, self.days[0], self.stamp)
        self.assertEqual(result["written"], 0)
        self.assertTrue(result["dry_run"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
