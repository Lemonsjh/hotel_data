"""JY01：完整日窗口、免费房/手工调整、原生对账与替换事务。"""
import sqlite3
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "OTA采集服务/foxhis_pms"))
import foxhis_jy01_data as jy
from foxhis_common import Config, FoxhisError, SOURCE_PLATFORM
from .test_realtime_forecast import DatabaseAdapter


class JY01Tests(unittest.TestCase):
    def setUp(self):
        self.stamp = datetime(2026, 10, 19, 15)
        self.days = jy.collection_dates(self.stamp.date())
        self.config = Config(hotel_id="TEST", hotel_name="测试酒店")
        self.metrics, self.rooms = [], []
        self.values = dict(total_rooms=61, maintain_rooms=6, locked_rooms=0, own_use_rooms=0,
                           available_rooms=55, room_nights=41, occupancy_rate="74.55",
                           room_revenue="4603.62", adr="112.28", free_rooms=0, revpar="75.47")
        self.room = dict(total_rooms="61", maintain_rooms="6", locked_rooms="0", own_use_rooms="0",
                         available_rooms="55", remaining_rooms="14", sold_rooms="41", free_rooms="0", room_revenue="4603.62")
        for day in self.days:
            self.metrics.extend({"business_date": day.strftime("%Y%m%d"), "metric_code": code,
                                 "metric_value": str(self.values[field])} for code, field in jy.CONTROL_FIELDS.items())
            self.rooms.extend({"business_date": day.strftime("%Y%m%d"), "gkey": "t", "hall": hall,
                               "room_type_code": code, **self.room} for code, hall in [("AR", "A"), ("{{{", "{")])
        self.db = DatabaseAdapter()
        columns = ",".join(f"`{c}` TEXT" for c in jy.COLUMNS)
        self.db.db.execute(f"CREATE TABLE jy01_hotel_statistics_daily ({columns}, UNIQUE(hotel_name,source_platform,business_date,dimension_type,dimension_name))")
        self.db.db.commit()
        self.connection_patch = patch.object(jy, "mysql_connection", return_value=self.db)
        self.connection_patch.start()

    def tearDown(self):
        self.connection_patch.stop()
        self.db.db.close()

    def build(self, metrics=None, rooms=None):
        return jy.build_rows(self.metrics if metrics is None else metrics, self.rooms if rooms is None else rooms, self.config, self.stamp)

    def seed(self, row):
        sql = "INSERT INTO jy01_hotel_statistics_daily (" + ",".join(jy.COLUMNS) + ") VALUES (" + ",".join(["%s"] * len(jy.COLUMNS)) + ")"
        self.db.executemany(sql, [tuple(row[c] for c in jy.COLUMNS)])
        self.db.commit()

    def contents(self):
        return self.db.db.execute("SELECT hotel_id,hotel_name,source_platform,business_date,dimension_type,room_revenue FROM jy01_hotel_statistics_daily ORDER BY hotel_id,business_date,dimension_type").fetchall()

    def test_window_crosses_month_year_and_leap_day(self):
        days = jy.collection_dates(date(2024, 3, 1))
        self.assertEqual((len(days), days[0], days[-1]), (30, date(2024, 1, 31), date(2024, 2, 29)))
        self.assertEqual(jy.collection_dates(date(2026, 1, 1))[-1], date(2025, 12, 31))

    def test_native_values_and_unmapped_order_count(self):
        rows, summary = self.build()
        self.assertEqual(len(rows), 30)
        self.assertEqual(rows[-1]["occupancy_rate"], Decimal("74.55"))
        self.assertEqual(rows[-1]["room_revenue"], Decimal("4603.62"))
        self.assertEqual(rows[-1]["sold_rooms"], 41)
        self.assertEqual(rows[-1]["remaining_rooms"], 14)
        self.assertIsNone(rows[-1]["orders_today"])
        self.assertIn("orders_today", summary["unmapped_fields"])

    def test_free_room_is_occupied_but_not_a_paid_night(self):
        # 西软 2026-09-30 的实际反例：37 间占用，1 间免费，计费出租 36。
        changed = dict(total_rooms=62, maintain_rooms=5, available_rooms=57, room_nights=36,
                       room_revenue="7363.63", free_rooms=1, occupancy_rate="63.16", adr="204.55", revpar="118.77")
        day = self.days[0].strftime("%Y%m%d")
        metrics = [{**r, "metric_value": str(changed[jy.CONTROL_FIELDS[r["metric_code"]]])}
                   if r["business_date"] == day and jy.CONTROL_FIELDS[r["metric_code"]] in changed else r for r in self.metrics]
        room_values = {k: str(v) for k, v in changed.items() if k in jy.ROOM_FIELDS}
        rooms = [{**r, **room_values, "sold_rooms": "37", "remaining_rooms": "20"} if r["business_date"] == day else r for r in self.rooms]
        rows, _ = self.build(metrics, rooms)
        self.assertEqual((rows[0]["room_nights"], rows[0]["sold_rooms"], rows[0]["remaining_rooms"]), (36, 37, 20))
        self.assertEqual(rows[0]["adr"], Decimal("204.55"))

    def test_manual_adjustment_is_included_in_hotel_total(self):
        day = self.days[0].strftime("%Y%m%d")
        rooms = [{**r, "room_revenue": "4503.62"} if r["business_date"] == day and r["room_type_code"] == "AR" else r for r in self.rooms]
        rooms.append({"business_date": day, "gkey": "t", "hall": "{", "room_type_code": "ZZZ",
                      **{f: "0" for f in jy.ROOM_FIELDS}, "room_revenue": "100"})
        rows, summary = self.build(rooms=rooms)
        self.assertEqual(rows[0]["room_revenue"], Decimal("4603.62"))
        self.assertEqual(summary["manual_adjustments_included"][self.days[0].isoformat()]["room_revenue"], Decimal(100))

    def test_missing_day_or_metric_and_duplicates_fail(self):
        for records in (self.metrics[:-1], self.metrics[11:], [*self.metrics, self.metrics[0]]):
            with self.assertRaises(FoxhisError): self.build(metrics=records)
        for rooms in (self.rooms[:-1], [*self.rooms, self.rooms[0]]):
            with self.assertRaises(FoxhisError): self.build(rooms=rooms)

    def test_zero_denominators_follow_native_zero_metrics(self):
        rows, _ = self.build(
            [{**r, "metric_value": "0"} for r in self.metrics],
            [{**r, **{f: "0" for f in jy.ROOM_FIELDS}} for r in self.rooms])
        self.assertEqual((rows[0]["room_count"], rows[0]["occupancy_rate"], rows[0]["adr"], rows[0]["revpar"]), (0, 0, 0, 0))

    def test_signed_net_revenue_is_retained(self):
        metrics = []
        for r in self.metrics:
            field = jy.CONTROL_FIELDS[r["metric_code"]]
            metrics.append({**r, "metric_value": str(-Decimal(r["metric_value"]))} if field in ("room_revenue", "adr", "revpar") else r)
        rooms = [{**r, "room_revenue": "-4603.62"} for r in self.rooms]
        rows, _ = self.build(metrics, rooms)
        self.assertEqual(rows[0]["room_revenue"], Decimal("-4603.62"))

    def test_unknown_day_dimension_or_metric_fails(self):
        for key, value in (("business_date", "20261019"), ("metric_code", "UNKNOWN")):
            records = [{**self.metrics[0], key: value}, *self.metrics[1:]]
            with self.assertRaises(FoxhisError): self.build(metrics=records)
        with self.assertRaises(FoxhisError): self.build(rooms=[{**self.rooms[0], "gkey": "m"}, *self.rooms[1:]])

    def test_bad_counts_and_nonfinite_numbers_fail(self):
        for value in ("NaN", "Infinity", "", "-1", "1.5"):
            records = [{**r, "metric_value": value} if r["metric_code"] == "010100" else r for r in self.metrics]
            with self.assertRaises(FoxhisError): self.build(metrics=records)

    def test_journal_formulas_and_room_detail_mismatches_fail(self):
        for code in ("012000", "014000", "018560", "013000", "011000"):
            records = [{**r, "metric_value": "999"} if r["metric_code"] == code else r for r in self.metrics]
            with self.assertRaises(FoxhisError): self.build(metrics=records)
        rooms = [{**self.rooms[0], "room_revenue": "1"}, *self.rooms[1:]]
        with self.assertRaises(FoxhisError): self.build(rooms=rooms)

    def test_source_business_day_must_be_closed(self):
        with patch.object(jy, "require_columns"), patch.object(jy, "query", return_value=[{"current_business_date": "20261018"}]):
            with self.assertRaises(FoxhisError): jy.fetch_rows(self.config, self.stamp)

    def test_scoped_replacement_is_idempotent(self):
        rows, _ = self.build()
        self.seed({**rows[0], "room_revenue": Decimal(1)})
        self.seed({**rows[0], "hotel_id": "OTHER", "hotel_name": "其他酒店"})
        self.seed({**rows[0], "source_platform": "PMS（宝寓）"})
        self.seed({**rows[0], "business_date": self.days[0] - timedelta(days=1)})
        self.seed({**rows[0], "dimension_type": "房型", "dimension_name": "大床房"})
        jy.sync_rows(rows, self.config); jy.sync_rows(rows, self.config)
        self.assertEqual(len(self.contents()), 34)

    def test_other_hotel_with_same_name_collision_rolls_back(self):
        rows, _ = self.build()
        self.seed({**rows[0], "room_revenue": Decimal(1)})
        self.seed({**rows[1], "hotel_id": "OTHER"})
        before = self.contents()
        with self.assertRaises(sqlite3.IntegrityError): jy.sync_rows(rows, self.config)
        self.assertEqual(self.contents(), before)

    def test_invalid_empty_duplicate_mixed_or_stale_batch_preserves_data(self):
        rows, _ = self.build()
        self.seed({**rows[0], "snapshot_time": self.stamp + timedelta(seconds=1)})
        before = self.contents()
        for invalid in ([], rows[:-1], [rows[0]] * 30, [{**r, "source_platform": "OTHER"} for r in rows], rows):
            with self.assertRaises(FoxhisError): jy.sync_rows(invalid, self.config)
        self.assertEqual(self.contents(), before)

    def test_dry_run_never_writes(self):
        rows, summary = self.build()
        with patch.object(jy, "fetch_rows", return_value=(rows, summary)), patch.object(jy, "sync_rows", side_effect=AssertionError("不应写入")), patch.object(jy, "save_output"), patch.object(sys, "argv", ["jy01", "--dry-run"]):
            result = jy.collect(self.config, self.days[-1], self.stamp)
        self.assertEqual(result["written"], 0)
        self.assertTrue(result["dry_run"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
