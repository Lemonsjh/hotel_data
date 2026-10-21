"""JY03：月度窗口、日累计/返减、各维度对账和替换范围。"""
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
        self.dictionaries = {"M": {"OTA": "携程", "COM": "免费房", "HSE": "自用房"}, "C": {"WKI": "上门散客"}}
        self.records, self.rooms, self.facts, self.groups = self.fixture()
        self.db = DatabaseAdapter()
        columns = ",".join(f"`{c}` TEXT" for c in jy.COLUMNS)
        self.db.db.execute(f"CREATE TABLE jy03_hotel_statistics_month ({columns}, UNIQUE(hotel_name,source_platform,period_month,dimension_type,dimension_name))")
        self.db.db.commit()
        self.db_patch = patch.object(jy, "mysql_connection", return_value=self.db)
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop(); self.db.db.close()

    def fixture(self, fractional=False):
        records, rooms, facts, groups, cumulative = [], [], [], [], {}
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
            values = dict(total_rooms=str(capacity), maintain_rooms=str(maintain), locked_rooms="0", own_use_rooms="0",
                          available_rooms=str(capacity-maintain), remaining_rooms=str(capacity-maintain-int(paid+free)),
                          sold_rooms=str(paid+free), free_rooms=str(free), room_revenue=str(gross+rebate))
            for code, hall, name in [("AR", "A", "历史双床房"), ("{{{", "{", "总计")]:
                rooms.append({"business_date": day, "gkey": "t", "hall": hall, "room_type_code": code, "room_type_name": name, **values})
            for market, raw, billable, fee in [("OTA", paid, paid, gross+rebate), ("COM", free, D(0), D(0))]:
                facts.append({"business_date": day, "market_code": market, "channel_code": "WKI", "raw_nights": str(raw), "paid_nights": str(billable), "room_revenue": str(fee)})
                groups.append({"business_date": day, "class": "M", "grp": "OTA", "code": market, "room_nights": str(raw), "room_revenue": str(fee)})
            groups.append({"business_date": day, "class": "C", "grp": "", "code": "WKI", "room_nights": str(paid+free), "room_revenue": str(gross+rebate)})
        return records, rooms, facts, groups

    def build(self, records=None, rooms=None, facts=None, groups=None, dictionaries=None):
        return jy.build_month(self.records if records is None else records, self.rooms if rooms is None else rooms,
                              self.facts if facts is None else facts, self.groups if groups is None else groups,
                              self.dictionaries if dictionaries is None else dictionaries, self.config, self.month, self.end, self.stamp)

    def seed(self, row):
        sql = "INSERT INTO jy03_hotel_statistics_month (" + ",".join(jy.COLUMNS) + ") VALUES (" + ",".join(["%s"] * len(jy.COLUMNS)) + ")"
        self.db.executemany(sql, [tuple(row[c] for c in jy.COLUMNS)]); self.db.commit()

    def contents(self):
        return self.db.db.execute("SELECT hotel_id,source_platform,period_month,dimension_type,dimension_name,room_revenue FROM jy03_hotel_statistics_month ORDER BY hotel_id,source_platform,period_month,dimension_type,dimension_name").fetchall()

    def test_initial_six_months_then_only_current_and_year_boundary(self):
        self.assertEqual([d.isoformat() for d in jy.collection_months(date(2026, 1, 5), True)], ["2026-01-01", "2025-12-01", "2025-11-01", "2025-01-01", "2024-12-01", "2024-11-01"])
        self.assertEqual(jy.collection_months(self.stamp.date(), False), [self.month])

    def test_closed_period_same_day_prior_year_and_first_day_empty(self):
        self.assertEqual(jy.month_end(date(2026, 9, 1), self.stamp.date()), date(2026, 9, 30))
        self.assertEqual(jy.month_end(date(2025, 10, 1), self.stamp.date()), date(2025, 10, 2))
        self.assertEqual(jy.month_end(date(2023, 2, 1), date(2024, 2, 29)), date(2023, 2, 28))
        self.assertLess(jy.month_end(self.month, self.month), self.month)
        self.assertLess(jy.month_end(date(2025, 10, 1), self.month), date(2025, 10, 1))

    def test_net_rebate_weighted_rates_and_all_dimensions_balance(self):
        rows, audit = self.build(); total = rows[0]
        self.assertEqual((total["room_count"], total["maintain_rooms"], total["room_nights"], total["room_revenue"]), (30, 3, D(14), D(390)))
        self.assertEqual((total["adr"], total["revpar"], total["occupancy_rate"]), (D("27.86"), D(13), D("51.85")))
        self.assertEqual(audit["room_rebate"], D(-10))
        for kind in jy.DIMENSIONS[1:]:
            self.assertEqual(sum(r["room_revenue"] for r in rows if r["dimension_type"] == kind), D(390))
            self.assertEqual(sum(r["room_nights"] for r in rows if r["dimension_type"] == kind), D(14))
        channel = next(r for r in rows if r["dimension_type"] == "渠道")
        self.assertEqual(channel["dimension_name"], "上门散客")
        self.assertIsNone(channel["room_count"])
        self.assertIsNone(channel["occupancy_rate"])

    def test_fractional_billable_nights_are_preserved(self):
        rows, _ = self.build(*self.fixture(fractional=True))
        self.assertEqual(rows[0]["room_nights"], D("14.5"))

    def test_native_historical_names_are_split_within_month(self):
        rooms = [{**r, "room_type_name": "更名前房型"} if r["business_date"] == "20261001" and r["room_type_code"] == "AR" else r for r in self.rooms]
        rows, _ = self.build(rooms=rooms)
        self.assertEqual({r["dimension_name"] for r in rows if r["dimension_type"] == "房型"}, {"更名前房型", "历史双床房"})

    def test_unassigned_adjustments_do_not_disappear(self):
        facts = [{**r, "market_code": "", "channel_code": ""} if r["market_code"] == "OTA" else r for r in self.facts]
        groups = []
        for r in self.groups:
            if r["class"] == "M":groups.append({**r, "code": "zzz"} if r["code"] == "OTA" else r)
            else:
                paid = D(4) if r["business_date"] == "20261001" else D(10)
                groups.extend([{**r, "room_nights": str(D(r["room_nights"])-paid), "room_revenue": "0"}, {**r, "code": "zzz", "room_nights": str(paid)}])
        rows, _ = self.build(facts=facts, groups=groups)
        self.assertIn("未分配客源", {r["dimension_name"] for r in rows})
        self.assertIn("未分配渠道", {r["dimension_name"] for r in rows})

    def test_missing_day_duplicate_metric_and_wrong_month_fail(self):
        for records in (self.records[:-1], self.records[11:], [*self.records, self.records[0]], [{**self.records[0], "business_date": "20260930"}, *self.records[1:]]):
            with self.assertRaises(FoxhisError): self.build(records=records)

    def test_month_cumulative_rebate_or_rate_mismatch_fails(self):
        for code, field in [("013000", "month_value"), ("013000", "month_rebate"), ("014000", "month_value"), ("012000", "month_value")]:
            records = [{**r, field: "999"} if r["business_date"] == "20261002" and r["metric_code"] == code else r for r in self.records]
            with self.assertRaises(FoxhisError): self.build(records=records)

    def test_missing_room_detail_or_group_mismatch_fails(self):
        for rooms in (self.rooms[:-1], [*self.rooms, self.rooms[0]], [{**self.rooms[0], "room_revenue": "999"}, *self.rooms[1:]]):
            with self.assertRaises(FoxhisError): self.build(rooms=rooms)
        with self.assertRaises(FoxhisError): self.build(groups=[{**self.groups[0], "room_revenue": "999"}, *self.groups[1:]])

    def test_unknown_dictionary_invalid_or_duplicate_fact_fails(self):
        with self.assertRaises(FoxhisError): self.build(dictionaries={"M": {}, "C": {"WKI": "上门散客"}})
        for facts in ([*self.facts, self.facts[0]], [{**self.facts[0], "paid_nights": "999"}, *self.facts[1:]], [{**self.facts[0], "room_revenue": "NaN"}, *self.facts[1:]]):
            with self.assertRaises(FoxhisError): self.build(facts=facts)

    def test_refresh_replaces_only_current_month_and_managed_dimensions(self):
        rows, _ = self.build()
        self.seed({**rows[0], "period_month": "2026-09"})
        self.seed({**rows[0], "dimension_type": "入住类型", "dimension_name": "日租"})
        self.seed({**rows[0], "hotel_id": "OTHER", "hotel_name": "其他酒店"})
        self.seed({**rows[0], "source_platform": "PMS（宝寓）"})
        self.seed({**rows[0], "room_revenue": D(1)})
        jy.sync_rows(rows, self.config); jy.sync_rows(rows, self.config)
        self.assertEqual(len(self.contents()), len(rows)+4)

    def test_same_name_other_hotel_collision_rolls_back(self):
        rows, _ = self.build()
        self.seed({**rows[0], "room_revenue": D(1)})
        self.seed({**rows[1], "hotel_id": "OTHER"})
        before = self.contents()
        with self.assertRaises(sqlite3.IntegrityError): jy.sync_rows(rows, self.config)
        self.assertEqual(self.contents(), before)

    def test_empty_mixed_duplicate_missing_total_and_stale_batches_preserve_data(self):
        rows, _ = self.build(); self.seed({**rows[0], "snapshot_time": self.stamp+timedelta(seconds=1)})
        before = self.contents()
        for invalid in ([], [*rows, rows[0]], rows[1:], [r for r in rows if r["dimension_type"] != "渠道"], [{**r, "period_month": "2020-01"} for r in rows], [{**r, "source_platform": "OTHER"} for r in rows], rows):
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
