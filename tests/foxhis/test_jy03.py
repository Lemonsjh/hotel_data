"""JY03：月度窗口、日累计/返减、总营业指标和替换范围。"""
import sqlite3
import sys
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "OTA采集服务/foxhis_pms"))
import foxhis_jy03_data as jy
from foxhis_common import Config, FoxhisError, SOURCE_PLATFORM
from .test_realtime_forecast import DatabaseAdapter


class JY03Tests(unittest.TestCase):
    def setUp(self):
        self.stamp = datetime(2026, 10, 3, 15)
        self.month, self.end = date(2026, 10, 1), date(2026, 10, 2)
        self.config = Config(hotel_id="TEST", hotel_name="测试酒店")
        self.records = self.fixture()
        self.db = DatabaseAdapter()
        columns = ",".join(f"`{c}` TEXT" for c in jy.COLUMNS)
        self.db.db.execute(f"CREATE TABLE jy03_hotel_statistics_month ({columns}, UNIQUE(hotel_name,source_platform,period_month,dimension_type,dimension_name))")
        self.db.db.commit()
        self.db_patch = patch.object(jy, "mysql_connection", return_value=self.db)
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop(); self.db.db.close()

    def fixture(self, fractional=False):
        records, cumulative = [], {}
        for n, (capacity, maintain, paid, free, gross, rebate) in enumerate([(10, 1, D(4), D(1), D(100), D(-10)),
                                                                           (20, 2, D("10.5") if fractional else D(10), D(0), D(300), D(0))]):
            day = (self.month + timedelta(days=n)).strftime("%Y%m%d")
            m = {"010100": D(capacity), "010110": D(maintain), "010120": D(0), "010130": D(0),
                 "010190": D(capacity-maintain), "011000": paid, "013000": gross, "018530": free}
            m.update({"012000": jy.rounded(paid*100/m["010190"]), "014000": jy.rounded(gross/paid), "018560": jy.rounded(gross/m["010100"])})
            for code, value in m.items():
                if code not in ("012000", "014000", "018560"):
                    cumulative[code] = cumulative.get(code, D(0)) + value
            cumulative.update({"012000": jy.rounded(cumulative["011000"]*100/cumulative["010190"]),
                               "014000": jy.rounded(cumulative["013000"]/cumulative["011000"]),
                               "018560": jy.rounded(cumulative["013000"]/cumulative["010100"])})
            for code, value in m.items():
                records.append({"business_date": day, "metric_code": code, "metric_value": str(value), "month_value": str(cumulative[code]),
                                "daily_rebate": str(rebate if code == "013000" else 0), "month_rebate": "-10" if code == "013000" else "0"})
        return records

    def build(self, records=None):
        return jy.build_month(self.records if records is None else records,
                              self.config, self.month, self.end, self.stamp)

    def seed(self, row):
        sql = "INSERT INTO jy03_hotel_statistics_month (" + ",".join(jy.COLUMNS) + ") VALUES (" + ",".join(["%s"] * len(jy.COLUMNS)) + ")"
        self.db.executemany(sql, [tuple(row[c] for c in jy.COLUMNS)]); self.db.commit()

    def contents(self):
        return self.db.db.execute("SELECT hotel_id,source_platform,period_month,dimension_type,dimension_name,room_revenue FROM jy03_hotel_statistics_month ORDER BY hotel_id,source_platform,period_month,dimension_type,dimension_name").fetchall()

    def test_initial_six_months_then_current_and_prior_year_with_year_boundary(self):
        self.assertEqual([d.isoformat() for d in jy.collection_months(date(2026, 1, 5), True)], ["2026-01-01", "2025-12-01", "2025-11-01", "2025-01-01", "2024-12-01", "2024-11-01"])
        self.assertEqual(jy.collection_months(self.stamp.date(), False), [self.month, date(2025, 10, 1)])
        self.assertEqual(jy.collection_months(date(2026, 1, 5), False), [date(2026, 1, 1), date(2025, 1, 1)])

    def test_closed_period_same_day_prior_year_and_first_day_empty(self):
        self.assertEqual(jy.month_end(date(2026, 9, 1), self.stamp.date()), date(2026, 9, 30))
        self.assertEqual(jy.month_end(date(2025, 10, 1), self.stamp.date()), date(2025, 10, 2))
        self.assertEqual(jy.month_end(date(2023, 2, 1), date(2024, 2, 29)), date(2023, 2, 28))
        self.assertLess(jy.month_end(self.month, self.month), self.month)
        self.assertLess(jy.month_end(date(2025, 10, 1), self.month), date(2025, 10, 1))

    def test_net_rebate_weighted_rates_and_only_total(self):
        rows, audit = self.build(); total = rows[0]
        self.assertEqual((total["room_count"], total["maintain_rooms"], total["room_nights"], total["room_revenue"]), (30, 3, D(14), D(390)))
        self.assertEqual((total["adr"], total["revpar"], total["occupancy_rate"]), (D("27.86"), D(13), D("51.85")))
        self.assertEqual(audit["room_rebate"], D(-10))
        self.assertEqual(len(rows), 1)
        self.assertEqual((total['dimension_type'], total['dimension_name']), (jy.TOTAL, jy.TOTAL))

    def test_fractional_billable_nights_are_preserved(self):
        rows, _ = self.build(self.fixture(fractional=True))
        self.assertEqual(rows[0]["room_nights"], D("14.5"))

    def test_missing_day_duplicate_metric_and_wrong_month_fail(self):
        for records in (self.records[:-1], self.records[11:], [*self.records, self.records[0]], [{**self.records[0], "business_date": "20260930"}, *self.records[1:]]):
            with self.assertRaises(FoxhisError): self.build(records=records)

    def test_month_cumulative_rebate_or_rate_mismatch_fails(self):
        for code, field in [("013000", "month_value"), ("013000", "month_rebate"), ("014000", "month_value"), ("012000", "month_value")]:
            records = [{**r, field: "999"} if r["business_date"] == "20261002" and r["metric_code"] == code else r for r in self.records]
            with self.assertRaises(FoxhisError): self.build(records=records)

    def test_invalid_daily_capacity_or_nights_are_rejected(self):
        for code, value in [('010100', '10.5'), ('010190', '8'), ('011000', '-1'), ('018530', '-1')]:
            records = [{**r, 'metric_value': value} if r['business_date'] == '20261001' and r['metric_code'] == code else r
                       for r in self.records]
            with self.assertRaises(FoxhisError):
                self.build(records)

    def test_refresh_replaces_only_total_and_preserves_old_dimensions(self):
        rows, _ = self.build()
        self.seed({**rows[0], "period_month": "2026-09"})
        self.seed({**rows[0], "dimension_type": "入住类型", "dimension_name": "日租"})
        for kind in ("房型", "客源", "渠道"):
            self.seed({**rows[0], "dimension_type": kind, "dimension_name": "旧维度"})
        self.seed({**rows[0], "hotel_id": "OTHER", "hotel_name": "其他酒店"})
        self.seed({**rows[0], "source_platform": "PMS（宝寓）"})
        self.seed({**rows[0], "room_revenue": D(1)})
        jy.sync_rows(rows, self.config); jy.sync_rows(rows, self.config)
        self.assertEqual(len(self.contents()), len(rows)+7)

    def test_two_month_refresh_updates_both_and_preserves_other_months(self):
        rows, _ = self.build()
        historical = [{**r, 'period_month': '2025-10'} for r in rows]
        self.seed({**rows[0], 'room_revenue': D(1)})
        self.seed({**historical[0], 'room_revenue': D(2)})
        self.seed({**rows[0], 'period_month': '2026-09', 'room_revenue': D(3)})
        jy.sync_rows(rows + historical, self.config)
        totals = {r[2]: r[5] for r in self.contents() if r[3] == jy.TOTAL}
        self.assertEqual({k: D(v) for k, v in totals.items()},
                         {'2025-10': D(390), '2026-10': D(390), '2026-09': D(3)})
        self.assertEqual(len(self.contents()), len(rows) * 2 + 1)

    def test_refresh_fetches_matching_closed_windows_for_both_years(self):
        windows = []
        def source(sql, config, **kwargs):
            if 'from sysdata' in sql:
                return [{'current_business_date': '20261003'}]
            if 'from yjourrep' in sql:
                windows.append(sql)
                return [{'nonempty': 'true'}]
            raise AssertionError("不应查询明细或字典：" + sql)
        calls = []
        def build(*args):
            calls.append((args[2], args[3]))
            return [], {}
        with patch.object(jy, 'require_columns'), patch.object(jy, 'query', side_effect=source), patch.object(jy, 'build_month', side_effect=build):
            _, summary = jy.fetch_rows(self.config, self.stamp, initial=False)
        self.assertEqual(calls, [(date(2026, 10, 1), date(2026, 10, 2)),
                                 (date(2025, 10, 1), date(2025, 10, 2))])
        self.assertEqual(summary['requested_months'], ['2026-10', '2025-10'])
        self.assertTrue(all(start in sql and end in sql for sql, start, end in
                            zip(windows, ['20261001', '20251001'], ['20261003', '20251003'])))

    def test_same_name_other_hotel_collision_rolls_back(self):
        rows, _ = self.build()
        historical = {**rows[0], 'period_month': '2025-10'}
        self.seed({**historical, "room_revenue": D(1)})
        self.seed({**rows[0], "hotel_id": "OTHER"})
        before = self.contents()
        with self.assertRaises(sqlite3.IntegrityError): jy.sync_rows([historical, *rows], self.config)
        self.assertEqual(self.contents(), before)

    def test_empty_mixed_duplicate_missing_total_and_stale_batches_preserve_data(self):
        rows, _ = self.build(); self.seed({**rows[0], "snapshot_time": self.stamp+timedelta(seconds=1)})
        before = self.contents()
        for invalid in ([], [*rows, rows[0]], rows[1:], [{**rows[0], "dimension_type": "房型"}], [{**rows[0], "dimension_name": "其他名称"}], [{**r, "period_month": "2020-01"} for r in rows], [{**r, "source_platform": "OTHER"} for r in rows], rows):
            with self.assertRaises(FoxhisError): jy.sync_rows(invalid, self.config)
        self.assertEqual(self.contents(), before)

    def test_collect_initial_and_subsequent_selection_and_dry_run(self):
        rows, audit = self.build()
        for existing in (False, True):
            with patch.object(jy, "has_monthly_data", return_value=existing), patch.object(jy, "fetch_rows", return_value=(rows, {"by_month": audit})) as fetch, patch.object(jy, "sync_rows", side_effect=AssertionError("不应写入")), patch.object(jy, "save_output"), patch.object(sys, "argv", ["jy03", "--dry-run"]):
                result = jy.collect(self.config, self.end, self.stamp)
            self.assertEqual(fetch.call_args.args[2], not existing)
            self.assertEqual(result["written"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
