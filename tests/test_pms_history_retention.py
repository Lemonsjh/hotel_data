"""宝寓和西软各六个历史写入口的隔离清理、边界与事务回滚。"""
import json
import os
import re
import sqlite3
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests import test_meituan_pms_retention as fixtures
from tests import test_meituan_pms_booking_report as bookings
from tests import test_meituan_pms_room_performance_report as room_daily
from tests import test_bypms_daily_report as by_daily
from tests import test_bypms_room_status as by_status
from tests import test_bypms_jy03 as by_monthly
from tests import test_bypms_jd01 as by_booking
from tests import test_bypms_rs01 as by_revenue
from OTA采集服务.pms_history_retention import RULES, cleanup_history, cleanup_rows
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "OTA采集服务/foxhis_pms"))
import foxhis_common
import foxhis_room_status_data as fx_status
import foxhis_jl01_data as fx_room_daily
import foxhis_jy01_data as fx_daily
import foxhis_jy03_data as fx_monthly
import foxhis_jd01_data as fx_booking
import foxhis_room_revenue_data as fx_revenue


class Database(fixtures.Database):
    KEYS = {**fixtures.Database.KEYS,
            "jl01_room_type_performance_daily": "hotel_id,business_date,room_type_name",
            "jd01_booking_detail": "hotel_id,order_id,room_type_name,booking_time"}

    def __init__(self, table, row):
        super().__init__(table, {**row, "created_at": None})

    def executemany(self, sql, rows):
        if rows and not isinstance(rows[0], dict):
            columns = [name.strip(' `') for name in re.search(r"\(([^)]+)\)\s+VALUES", sql)[1].split(',')]
            keys = iter(columns)
            sql = re.sub(r"%s", lambda _: f"%({next(keys)})s", sql)
            rows = [dict(zip(columns, row)) for row in rows]
        super().executemany(sql, rows)

    def fetchall(self):
        if self.query.startswith("SELECT snapshot_time"):
            return [(datetime.fromisoformat(stamp),) for stamp, in self.result.fetchall() if stamp]
        return super().fetchall()

    def close(self):
        pass


class PMSHistoryRetentionTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"HOTEL_ID": "hotel", "PMS_RETENTION": "{}"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def cases(self, provider):
        factories = [fixtures.rooms.MeituanPmsCollectorTests, room_daily.RoomPerformanceTests,
                     fixtures.daily.MeituanPmsDailyReportTests, fixtures.monthly.MeituanPmsMonthlyReportTests,
                     bookings.MeituanPmsBookingTests, fixtures.revenue.RevenueTests]
        methods = ["collect", "batch", "rows", "rows", "rows", "rows"]
        by_modules = [by_status.load_target(), by_daily.load_target(), by_daily.load_target(),
                      by_monthly.load_target(), by_booking.target(), by_revenue.load_target()]
        fx_modules = [fx_status, fx_room_daily, fx_daily, fx_monthly, fx_booking, fx_revenue]
        tables = ["pms_room_type_hourly_status", "jl01_room_type_performance_daily", "jy01_hotel_statistics_daily",
                  "jy03_hotel_statistics_month", "jd01_booking_detail", "rs01_room_revenue_daily"]
        source = "PMS（宝寓）" if provider == "bypms" else "PMS（Foxhis）"
        for index, (factory, method, table) in enumerate(zip(factories, methods, tables)):
            fixture = factory()
            fixture.setUp()
            rows = [{**row, "source_platform": source, "room_type_id": None} for row in getattr(fixture, method)()]
            if index == 4:
                rows = rows[:1]
            module = by_modules[index] if provider == "bypms" else fx_modules[index]
            stamp = rows[0]["snapshot_time"]
            config = foxhis_common.Config(hotel_id="hotel", hotel_name=rows[0]["hotel_name"])
            inventory = None
            if provider == "bypms" and index == 0:
                inventory = [{"hotel_id": "hotel", "hotel_name": rows[0]["hotel_name"],
                              "business_date": stamp.date(), "snapshot_time": stamp,
                              "snapshot_hour": rows[0]["snapshot_hour"], "bypms_room_type_id": "A",
                              "room_type_name": "大床房", "total_rooms": 10, "remaining_saleable_rooms": 4, "sold_rooms": 6}]
                rows = [{**module.standard_forecast_rows(inventory)[0], **module.standard_inventory_rows(inventory)[0]}]

            def write(db, rows=rows, index=index, module=module, config=config, inventory=inventory):
                if provider == "bypms":
                    if index == 0:
                        module.sync_mysql(inventory, rows[0]["snapshot_hour"], db)
                    elif index == 2:
                        module.sync_jy01(rows, db)
                    elif index == 4:
                        with patch.object(module.pymysql, "connect", return_value=db):
                            module.sync_mysql(rows, module.checkout_cutoff(rows[0]["snapshot_time"].date()))
                    else:
                        module.sync_mysql(rows, db)
                else:
                    with patch.object(module, "mysql_connection", return_value=db):
                        if index == 0:
                            module.sync_inventory(rows, config)
                        elif index == 5:
                            start, end = module.month_window(rows[0]["snapshot_time"].date())
                            module.sync_rows(rows, config, start, end)
                        else:
                            module.sync_rows(rows, config)
            yield table, rows, source, write

    def seed_expired(self, db, table, row, source):
        column = "booking_time" if table == "jd01_booking_detail" else RULES[table][0]
        expired = "2000-01" if column == "period_month" else datetime(2000, 1, 1)
        base = {**row, column: expired}
        if table == "jd01_booking_detail":
            base["departure_time"] = datetime(2000, 1, 2)
        elif table == "rs01_room_revenue_daily":
            base["checkout_time"] = datetime(2000, 1, 2)
        db.seed(table, base)
        db.seed(table, {**base, "hotel_id": "other", "hotel_name": "其他酒店"})
        discriminator = next(key for key in ("order_id", "dimension_name", "pms_room_type_id", "room_type_name") if key in row)
        db.seed(table, {**base, "source_platform": "其他PMS", discriminator: "其他来源"})
        return column, expired

    def test_all_twelve_writers_delete_only_expired_own_scope_and_keep_boundaries(self):
        for provider in ("bypms", "foxhis"):
            for table, rows, source, write in self.cases(provider):
                with self.subTest(provider=provider, table=table):
                    db = Database(table, rows[0])
                    self.addCleanup(db.db.close)
                    column, _ = self.seed_expired(db, table, rows[0], source)
                    capture = MagicMock()
                    cleanup_history(capture, table, "hotel", source, rows[0]["snapshot_time"])
                    cutoff = capture.execute.call_args.args[1][-1]
                    boundary = {**rows[0], column: cutoff}
                    if table == "jd01_booking_detail":
                        boundary["departure_time"] = datetime(2000, 1, 2)
                        boundary["order_id"] = "boundary"
                    if table == "rs01_room_revenue_daily":
                        boundary["checkout_time"] = datetime(2000, 1, 2)
                    db.seed(table, boundary)
                    write(db)
                    saved = db.db.execute(f"SELECT hotel_id,source_platform,`{column}` FROM `{table}`").fetchall()
                    own = [value for hotel, platform, value in saved if hotel == "hotel" and platform == source]
                    self.assertNotIn("2000-01", own)
                    self.assertFalse(any(value.startswith("2000-01-01") for value in own))
                    self.assertIn(db.value(cutoff), own)
                    self.assertTrue(any(hotel == "other" for hotel, _, _ in saved))
                    self.assertTrue(any(platform == "其他PMS" for _, platform, _ in saved))
                    write(db)
                    self.assertEqual(len(db.contents(table)), len(saved))

    def test_all_twelve_writers_roll_back_when_expired_cleanup_fails(self):
        for provider in ("bypms", "foxhis"):
            for table, rows, source, write in self.cases(provider):
                with self.subTest(provider=provider, table=table):
                    db = Database(table, rows[0])
                    self.addCleanup(db.db.close)
                    column, _ = self.seed_expired(db, table, rows[0], source)
                    before = db.contents(table)
                    db.db.execute(f"CREATE TRIGGER reject_cleanup BEFORE DELETE ON `{table}` "
                                  f"WHEN OLD.`{column}` < '2001' BEGIN SELECT RAISE(ABORT, 'cleanup failed'); END")
                    with self.assertRaises(sqlite3.IntegrityError):
                        write(db)
                    self.assertEqual(db.contents(table), before)
                    if table == "pms_room_type_hourly_status":
                        self.assertEqual(db.contents("pms_room_type_forecast"), [])

    def test_jd01_date_fallbacks_and_invalid_batches(self):
        db = Database("jd01_booking_detail", {"hotel_id": "hotel", "source_platform": "PMS（宝寓）",
                      "order_id": "old", "room_type_name": "房型", "booking_time": None,
                      "snapshot_time": None, "created_at": None})
        self.addCleanup(db.db.close)
        for index, column in enumerate(("booking_time", "snapshot_time", "created_at")):
            db.seed("jd01_booking_detail", {"hotel_id": "hotel", "source_platform": "PMS（宝寓）",
                    "order_id": str(index), "room_type_name": "房型", column: datetime(2000, 1, 1)})
        cleanup_history(db, "jd01_booking_detail", "hotel", "PMS（宝寓）", datetime(2026, 10, 29))
        self.assertEqual(db.contents("jd01_booking_detail"), [])
        for changes in ({"hotel_id": "other"}, {"source_platform": "其他PMS"}, {"snapshot_time": None}):
            cursor = MagicMock()
            row = {"hotel_id": "hotel", "source_platform": "PMS（宝寓）", "snapshot_time": datetime(2026, 10, 29)}
            with self.assertRaises(ValueError):
                cleanup_rows(cursor, "jd01_booking_detail", [row, {**row, **changes}], "hotel", "PMS（宝寓）")
            cursor.execute.assert_not_called()
        for platform in ("bypms", "foxhis"):
            env = fixtures.rooms.runner.build_env({"data_retention": {"pms_daily_days": 600}}, platform)
            self.assertEqual(json.loads(env["PMS_RETENTION"])["pms_daily_days"], 600)
