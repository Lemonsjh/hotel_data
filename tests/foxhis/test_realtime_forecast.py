"""实时房态回归测试；SQLite 仅验证替换事务，不能替代 MySQL 集成验收。"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "OTA采集服务/foxhis_pms"))
import foxhis_common as common
import foxhis_room_status_data as status


class DatabaseAdapter:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        columns = ", ".join(f"`{name}` TEXT" for name in status.FORECAST_COLUMNS)
        self.db.execute(f"CREATE TABLE pms_room_type_forecast ({columns}, PRIMARY KEY (hotel_id, stay_date, pms_room_type_id))")
        hourly_columns = ", ".join(f"`{name}` TEXT" for name in status.HOURLY_COLUMNS)
        self.db.execute(f"CREATE TABLE pms_room_type_hourly_status ({hourly_columns}, UNIQUE (hotel_id, snapshot_hour, pms_room_type_id))")
        self.db.commit()

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    @staticmethod
    def values(args):
        return tuple(value.isoformat(sep=" ") if isinstance(value, datetime)
                     else value.isoformat() if isinstance(value, date)
                     else str(value) if isinstance(value, Decimal) else value for value in args)

    def execute(self, sql, args):
        self.current = self.db.execute(sql.replace(" FOR UPDATE", "").replace("%s", "?"), self.values(args))

    def executemany(self, sql, args):
        self.db.executemany(sql.replace("%s", "?"), [self.values(row) for row in args])

    def fetchall(self):
        return [(datetime.fromisoformat(row[0]),) for row in self.current.fetchall()]

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()

    def close(self):
        pass  # 测试结束后关闭底层内存库，便于检查回滚后的数据。


class RealtimeForecastTests(unittest.TestCase):
    def setUp(self):
        self.config = common.Config(hotel_id="HOTEL_A", hotel_name="测试酒店")
        self.stamp = datetime(2026, 10, 19, 0, 15)
        self.db = DatabaseAdapter()
        self.connection_patch = patch.object(status, "mysql_connection", return_value=self.db)
        self.connection_patch.start()

    def tearDown(self):
        self.connection_patch.stop()
        self.db.db.close()

    def rooms(self):
        records = [
            {"room_no": "101", "room_type_code": "A", "room_status": "R", "occupy_status": "V", "locked": "N"},
            {"room_no": "102", "room_type_code": "A", "room_status": "D", "occupy_status": "V", "locked": "N"},
            {"room_no": "103", "room_type_code": "A", "room_status": "D", "occupy_status": "O", "locked": "N"},
            {"room_no": "104", "room_type_code": "A", "room_status": "O", "occupy_status": "V", "locked": "L"},
            {"room_no": "201", "room_type_code": "B", "room_status": "S", "occupy_status": "V", "locked": "L"},
        ]
        return status.build_rows(records, {"A": "大床房", "B": "双床房"}, self.config, self.stamp.date(), self.stamp)

    def forecasts(self):
        return status.build_forecast_rows(self.rooms(), self.stamp)

    def seed(self, row):
        sql = "INSERT INTO pms_room_type_forecast (" + ",".join(status.FORECAST_COLUMNS) + ") VALUES (" + ",".join(["%s"] * len(status.FORECAST_COLUMNS)) + ")"
        self.db.executemany(sql, [tuple(row[name] for name in status.FORECAST_COLUMNS)])
        self.db.commit()

    def contents(self):
        return self.db.db.execute("SELECT hotel_id,source_platform,stay_date,pms_room_type_id,total_rooms FROM pms_room_type_forecast ORDER BY hotel_id,stay_date,pms_room_type_id").fetchall()

    def test_current_inventory_excludes_repair_and_locked_rooms(self):
        a, b = self.forecasts()
        self.assertEqual((a["total_rooms"], a["available_rooms"], a["occupied_rooms"]), (4, 2, 1))
        self.assertEqual((b["total_rooms"], b["available_rooms"], b["occupied_rooms"]), (1, 0, 0))
        self.assertEqual(a["stay_date"], self.stamp.date())
        for field in ("overbooking_rooms", "room_revenue", "adr", "revpar"):
            self.assertIsNone(a[field])

    def test_replacement_removes_history_and_disappeared_types_but_preserves_other_scopes(self):
        a, _ = self.forecasts()
        self.seed({**a, "stay_date": date(2026, 10, 18), "snapshot_time": self.stamp - timedelta(days=1)})
        self.seed({**a, "pms_room_type_id": "REMOVED", "snapshot_time": self.stamp - timedelta(minutes=1)})
        self.seed({**a, "hotel_id": "OTHER_HOTEL"})
        self.seed({**a, "source_platform": "PMS（宝寓）", "pms_room_type_id": "OTHER_SOURCE"})
        self.assertEqual(status.sync_inventory(self.forecasts(), self.config), 2)
        self.assertEqual(status.sync_inventory(self.forecasts(), self.config), 2)
        rows = self.contents()
        self.assertEqual(len(rows), 4)
        own = [row for row in rows if row[0] == "HOTEL_A" and row[1] == common.SOURCE_PLATFORM]
        self.assertEqual({(row[2], row[3]) for row in own}, {("2026-10-19", "A"), ("2026-10-19", "B")})

    def test_insert_conflict_rolls_back_deletion(self):
        a, _ = self.forecasts()
        self.seed({**a, "stay_date": date(2026, 10, 18), "snapshot_time": self.stamp - timedelta(days=1)})
        self.seed({**a, "source_platform": "PMS（宝寓）"})
        before = self.contents()
        with self.assertRaises(sqlite3.IntegrityError):
            status.sync_inventory(self.forecasts(), self.config)
        self.assertEqual(self.contents(), before)

    def test_empty_or_inconsistent_snapshot_does_not_delete(self):
        rows = self.forecasts()
        self.seed(rows[0])
        before = self.contents()
        for invalid in ([], [{**rows[0], "hotel_id": "OTHER"}], [{**rows[0], "stay_date": date(2026, 10, 18)}]):
            with self.assertRaises(common.FoxhisError):
                status.sync_inventory(invalid, self.config)
        self.assertEqual(self.contents(), before)

    def test_older_snapshot_cannot_replace_newer_snapshot(self):
        rows = self.forecasts()
        self.seed({**rows[0], "snapshot_time": self.stamp + timedelta(minutes=1)})
        before = self.contents()
        with self.assertRaises(common.FoxhisError):
            status.sync_inventory(rows, self.config)
        self.assertEqual(self.contents(), before)

    def test_unknown_codes_and_duplicate_rooms_fail_before_replacement(self):
        rooms = self.rooms()
        for invalid in ([*rooms, rooms[0]], [{**rooms[0], "_locked": "UNKNOWN"}], [{**rooms[0], "_occupy_status": "UNKNOWN"}]):
            with self.assertRaises(common.FoxhisError):
                status.build_forecast_rows(invalid, self.stamp)

    def test_realtime_uses_today_even_before_night_audit(self):
        config = common.Config(server="FoxFront", database="foxhis1", username="test", fetch_after="23:59", business_date_offset_days=1)
        with patch.object(common.Config, "from_env", return_value=config), patch.object(common, "check_reachable", return_value=(True, "ok")), patch.object(common, "after_gate", side_effect=AssertionError("实时房态不应调用夜审闸门")):
            seen = []
            def collect(config, day, stamp):
                seen.append((day, stamp.date()))
                return {"row_count": 0, "written": 0}
            common.report_main("room_status", collect, realtime=True)
        self.assertEqual(seen[0][0], seen[0][1])

    def test_dry_run_never_opens_mysql(self):
        records = [{"room_no": "101", "room_type_code": "A", "room_status": "R", "occupy_status": "V", "locked": "N"}]
        with patch.object(status, "require_columns"), patch.object(status, "room_type_name_map", return_value={"A": "大床房"}), patch.object(status, "room_status_name_map", return_value={"R": "干净"}), patch.object(status, "query", return_value=records), patch.object(status, "realtime_revenue", return_value={}), patch.object(status, "save_output"), patch.object(status, "sync_inventory", side_effect=AssertionError("不应落库")), patch.object(sys, "argv", ["room_status", "--dry-run"]):
            result = status.collect(self.config, date(2026, 10, 18), self.stamp)
        self.assertEqual(result["business_date"], "2026-10-19")

    def financial_record(self, line="1", amount="200.00", nights="2.00", room="101"):
        return {"folio_no": "F1", "line_no": line, "room_no": room, "charge_code": "1000", "amount": amount, "room_nights": nights, "ledger_source": "current"}

    def live_record(self, **changes):
        return {"room_no": "103", "room_type_code": "A", "folio_no": "F1", "room_quantity": "1", "share_code": "F", "rate_segment_id": "0", "rate_quantity": "1", "room_rate": "123.45", **changes}

    def test_live_price_matches_current_occupied_room_set(self):
        rows = self.forecasts()
        summary = status.apply_realtime_revenue(rows, self.rooms(), [self.live_record()])
        self.assertEqual(rows[0]["room_revenue"], Decimal("123.45"))
        self.assertEqual(rows[0]["adr"], Decimal("123.45"))
        self.assertEqual(rows[0]["revpar"], Decimal("30.86"))
        self.assertEqual(rows[1]["room_revenue"], Decimal("0.00"))
        self.assertIsNone(rows[1]["adr"])
        self.assertEqual(summary["priced_rooms"], 1)
        status.sync_inventory(rows, self.config)
        self.assertEqual(self.db.db.execute("SELECT room_revenue,adr,revpar FROM pms_room_type_forecast WHERE pms_room_type_id='A'").fetchone(), ("123.45", "123.45", "30.86"))

    def test_live_price_missing_room_or_missing_segment_fails(self):
        for records in ([], [self.live_record(rate_segment_id="")], [self.live_record(folio_no="")]):
            with self.assertRaises(common.FoxhisError):
                status.apply_realtime_revenue(self.forecasts(), self.rooms(), records)

    def test_live_price_duplicate_or_overlapping_segment_fails(self):
        with self.assertRaises(common.FoxhisError):
            status.apply_realtime_revenue(self.forecasts(), self.rooms(), [self.live_record(), self.live_record(rate_segment_id="1")])

    def test_live_price_rejects_vacant_room_or_different_physical_type(self):
        for changes in ({"room_no": "101"}, {"room_type_code": "B"}):
            with self.assertRaises(common.FoxhisError):
                status.apply_realtime_revenue(self.forecasts(), self.rooms(), [self.live_record(**changes)])

    def test_live_price_zero_is_valid_but_bad_prices_are_not(self):
        rows = self.forecasts()
        status.apply_realtime_revenue(rows, self.rooms(), [self.live_record(room_rate="0")])
        self.assertEqual(rows[0]["adr"], Decimal("0.00"))
        for rate in ("", "NaN", "Infinity", "-1"):
            with self.assertRaises(common.FoxhisError):
                status.apply_realtime_revenue(self.forecasts(), self.rooms(), [self.live_record(room_rate=rate)])

    def test_live_price_requires_verified_single_room_contract(self):
        for changes in ({"room_quantity": "2"}, {"rate_quantity": "2"}, {"share_code": "T"}):
            with self.assertRaises(common.FoxhisError):
                status.apply_realtime_revenue(self.forecasts(), self.rooms(), [self.live_record(**changes)])

    def test_live_price_without_occupied_rooms_does_not_need_orders(self):
        rooms = [{**row, "_occupy_status": "V"} for row in self.rooms()]
        rows = status.build_forecast_rows(rooms, self.stamp)
        status.apply_realtime_revenue(rows, rooms, [])
        self.assertTrue(all(row["room_revenue"] == Decimal("0.00") and row["adr"] is None for row in rows))

    def test_live_price_denominator_cannot_disagree_with_room_status(self):
        rows = self.forecasts(); rows[0]["occupied_rooms"] += 1
        with self.assertRaises(common.FoxhisError):
            status.apply_realtime_revenue(rows, self.rooms(), [self.live_record()])

    def test_financials_include_adjustments_and_use_billed_nights_instead_of_current_occupancy(self):
        rows = self.forecasts()
        records = [self.financial_record(), self.financial_record("2", "30.00", "0"), self.financial_record("3", "-10.00", "0")]
        summary = status.apply_posted_revenue(rows, self.rooms(), records)
        self.assertEqual(rows[0]["room_revenue"], Decimal("220.00"))
        self.assertEqual(rows[0]["adr"], Decimal("110.00"))
        self.assertEqual(rows[0]["revpar"], Decimal("55.00"))
        self.assertEqual(summary["by_room_type"]["A"]["room_nights"], Decimal("2.00"))

    def test_empty_posted_ledger_is_zero_revenue_but_undefined_adr(self):
        rows = self.forecasts()
        status.apply_posted_revenue(rows, self.rooms(), [])
        self.assertEqual(rows[0]["room_revenue"], Decimal("0.00"))
        self.assertIsNone(rows[0]["adr"])
        self.assertEqual(rows[0]["revpar"], Decimal("0.00"))

    def test_transferred_transactions_are_counted_once(self):
        rows = self.forecasts()
        record = self.financial_record()
        summary = status.apply_posted_revenue(rows, self.rooms(), [record, {**record, "ledger_source": "history"}])
        self.assertEqual(rows[0]["room_revenue"], Decimal("200.00"))
        self.assertEqual(summary["deduplicated_rows"], 1)

    def test_conflicting_duplicate_transactions_fail(self):
        record = self.financial_record()
        with self.assertRaises(common.FoxhisError):
            status.apply_posted_revenue(self.forecasts(), self.rooms(), [record, {**record, "amount": "201", "ledger_source": "history"}])

    def test_unknown_billed_room_is_not_silently_dropped(self):
        with self.assertRaises(common.FoxhisError):
            status.apply_posted_revenue(self.forecasts(), self.rooms(), [self.financial_record(room="UNKNOWN")])
        status.apply_posted_revenue(self.forecasts(), self.rooms(), [self.financial_record(amount="0", nights="0", room="UNKNOWN")])

    def test_invalid_financial_numbers_fail(self):
        for amount in ("", "unknown", "NaN", "Infinity"):
            with self.assertRaises(common.FoxhisError):
                status.apply_posted_revenue(self.forecasts(), self.rooms(), [self.financial_record(amount=amount)])

    def test_money_rounding_and_database_contract(self):
        rows = self.forecasts()
        status.apply_posted_revenue(rows, self.rooms(), [self.financial_record(amount="1,000.01", nights="3")])
        self.assertEqual(rows[0]["adr"], Decimal("333.34"))
        status.sync_inventory(rows, self.config)
        self.assertEqual(self.db.db.execute("SELECT room_revenue,adr,revpar FROM pms_room_type_forecast WHERE pms_room_type_id='A'").fetchone(), ("1000.01", "333.34", "250.00"))
        self.assertEqual(len(self.hourly_contents()), 2)

    def seed_hourly(self, row):
        sql = "INSERT INTO pms_room_type_hourly_status (" + ",".join(status.HOURLY_COLUMNS) + ") VALUES (" + ",".join(["%s"] * len(status.HOURLY_COLUMNS)) + ")"
        self.db.executemany(sql, [tuple(row[name] for name in status.HOURLY_COLUMNS)])
        self.db.commit()

    def hourly_contents(self):
        return self.db.db.execute("SELECT hotel_id,source_platform,snapshot_hour,pms_room_type_id,snapshot_time,total_rooms,available_rooms FROM pms_room_type_hourly_status ORDER BY hotel_id,snapshot_hour,pms_room_type_id").fetchall()

    def test_same_hour_replaces_latest_and_removes_disappeared_room_types(self):
        original = self.forecasts()
        status.sync_inventory(original, self.config)
        later = [{**original[0], "snapshot_time": self.stamp + timedelta(minutes=30), "available_rooms": 1}]
        status.sync_inventory(later, self.config)
        hourly = self.hourly_contents()
        self.assertEqual(len(hourly), 1)
        self.assertEqual(hourly[0][2:5], ("2026-10-19 00:00:00", "A", "2026-10-19 00:45:00"))
        self.assertEqual(hourly[0][6], "1")

    def test_next_hour_and_day_preserve_hourly_history(self):
        original = self.forecasts()
        for offset in (timedelta(), timedelta(hours=1), timedelta(days=1)):
            rows = [{**row, "snapshot_time": self.stamp + offset, "stay_date": (self.stamp + offset).date()} for row in original]
            status.sync_inventory(rows, self.config)
        self.assertEqual(len(self.hourly_contents()), 6)
        self.assertEqual({row[2] for row in self.contents()}, {"2026-10-20"})

    def test_hourly_preserves_other_hotels_and_sources(self):
        row = status.build_hourly_rows(self.forecasts())[0]
        self.seed_hourly({**row, "hotel_id": "OTHER_HOTEL"})
        self.seed_hourly({**row, "source_platform": "PMS（宝寓）", "pms_room_type_id": "OTHER_SOURCE"})
        status.sync_inventory(self.forecasts(), self.config)
        self.assertEqual(len(self.hourly_contents()), 4)

    def test_hourly_conflict_rolls_back_both_tables(self):
        original = self.forecasts()
        status.sync_inventory(original, self.config)
        hourly = status.build_hourly_rows(original)[0]
        self.seed_hourly({**hourly, "source_platform": "PMS（宝寓）", "pms_room_type_id": "CONFLICT"})
        before_daily, before_hourly = self.contents(), self.hourly_contents()
        later = [{**original[0], "snapshot_time": self.stamp + timedelta(minutes=1), "pms_room_type_id": "CONFLICT"}]
        with self.assertRaises(sqlite3.IntegrityError):
            status.sync_inventory(later, self.config)
        self.assertEqual(self.contents(), before_daily)
        self.assertEqual(self.hourly_contents(), before_hourly)

    def test_newer_hourly_snapshot_prevents_replacement_of_either_table(self):
        row = status.build_hourly_rows(self.forecasts())[0]
        self.seed_hourly({**row, "snapshot_time": self.stamp + timedelta(minutes=1)})
        before = self.hourly_contents()
        with self.assertRaises(common.FoxhisError):
            status.sync_inventory(self.forecasts(), self.config)
        self.assertEqual(self.contents(), [])
        self.assertEqual(self.hourly_contents(), before)

    def test_real_isql_output_keeps_first_row_and_single_column(self):
        rows = common.parse_isql_table("|type |occupy\n|-----|------\n|A    |V\n|B    |O\n")
        self.assertEqual(rows, [{"type": "A", "occupy": "V"}, {"type": "B", "occupy": "O"}])
        self.assertEqual(common.parse_isql_table("|name\n|----\n|rmsta\n"), [{"name": "rmsta"}])

    def test_zero_exit_code_sql_errors_still_fail(self):
        config = common.Config(isql_path=__file__, username="test")
        completed = SimpleNamespace(returncode=0, stdout="Msg 207, Level 16: Invalid column name", stderr="")
        with patch.object(common.subprocess, "run", return_value=completed) as run:
            with self.assertRaises(common.FoxhisError):
                common.run_isql("select missing_column from rmsta", config)
        self.assertNotIn("-b", run.call_args.args[0])
        self.assertIn("set char_convert off", run.call_args.kwargs["input"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
