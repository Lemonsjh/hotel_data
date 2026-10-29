"""保留边界、面板配置传递及五个写入口的真实事务回滚。"""
import json
import os
import re
import sqlite3
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from tests import test_meituan_pms_collector as rooms
from tests import test_meituan_pms_daily_report as daily
from tests import test_meituan_pms_monthly_report as monthly
from tests import test_meituan_pms_performance_report as performance
from tests import test_meituan_pms_revenue_report as revenue
from meituan_pms.retention import RULES, SOURCE, cleanup_history


class Database:
    KEYS = {
        "pms_room_type_forecast": "hotel_id,stay_date,pms_room_type_id",
        "pms_room_type_hourly_status": "hotel_id,snapshot_hour,pms_room_type_id",
        "jy01_hotel_statistics_daily": "hotel_name,source_platform,business_date,dimension_type,dimension_name",
        "jy03_hotel_statistics_month": "hotel_name,source_platform,period_month,dimension_type,dimension_name",
        "jl02_hotel_performance_daily": "hotel_id,business_date,category,metric_name",
        "rs01_room_revenue_daily": "hotel_id,order_id,business_date,room_no,charge_subject",
    }

    def __init__(self, table, row):
        self.db = sqlite3.connect(":memory:")
        tables = [table] if table != "pms_room_type_hourly_status" else [table, "pms_room_type_forecast"]
        columns = list(dict.fromkeys([*row, "room_type_id"]))
        for target in tables:
            fields = ', '.join(f"`{key}` TEXT" for key in columns)
            self.db.execute(f"CREATE TABLE `{target}` ({fields}, PRIMARY KEY({self.KEYS[target]}))")
        self.db.execute("CREATE TABLE hotel_room_type_mapping (hotel_id TEXT,source_platform TEXT,is_active INT,"
                        "source_product_id TEXT,source_room_type_name TEXT,room_type_id TEXT)")

    @staticmethod
    def value(value):
        return (value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
                if isinstance(value, date) else str(value) if isinstance(value, Decimal) else value)

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        self.query = sql
        self.result = self.db.execute(sql.replace(" FOR UPDATE", "").replace("%s", "?"),
                                      [self.value(value) for value in params])

    def fetchall(self):
        values = self.result.fetchall()
        if self.query.startswith("SELECT source_platform,"):
            return [(source, datetime.fromisoformat(stamp)) for source, stamp in values]
        if self.query.startswith("SELECT business_date,"):
            return [(date.fromisoformat(day), *rest) for day, *rest in values]
        return values

    def executemany(self, sql, rows):
        table = re.search(r"INSERT INTO `?(\w+)`?", sql)[1]
        sql = re.sub(r"%\((\w+)\)s", r":\1", sql)
        sql = sql.replace("ON DUPLICATE KEY UPDATE", f"ON CONFLICT({self.KEYS[table]}) DO UPDATE SET")
        sql = re.sub(r"VALUES\((\w+)\)", r"excluded.\1", sql)
        self.db.executemany(sql, [{key: self.value(value) for key, value in row.items()} for row in rows])

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()

    def seed(self, table, row):
        self.executemany(f"INSERT INTO `{table}` ({', '.join(row)}) VALUES "
                         f"({', '.join('%(' + key + ')s' for key in row)})", [row])
        self.commit()

    def contents(self, table):
        return self.db.execute(f"SELECT * FROM `{table}` ORDER BY 1,2,3,4,5").fetchall()


class MeituanRetentionTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"PMS_RETENTION": "{}"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def cases(self):
        specs = [
            (rooms.MeituanPmsCollectorTests, "collect", rooms.storage, rooms.storage.HOURLY,
             datetime(2026, 5, 1), "pms_room_type_id"),
            (daily.MeituanPmsDailyReportTests, "rows", daily.report, daily.report.TABLE,
             date(2025, 9, 23), "dimension_name"),
            (monthly.MeituanPmsMonthlyReportTests, "rows", monthly.report, monthly.report.TABLE,
             "2023-11", "dimension_name"),
            (performance.MeituanPmsPerformanceTests, "batch", performance.report, performance.report.TABLE,
             date(2025, 8, 4), "metric_name"),
            (revenue.RevenueTests, "rows", revenue.report, revenue.report.TABLE,
             date(2025, 9, 24), "order_id"),
        ]
        for factory, method, module, table, cutoff, discriminator in specs:
            fixture = factory()
            fixture.setUp()
            rows = getattr(fixture, method)()
            def write(db, module=module, rows=rows, fixture=fixture):
                if module is revenue.report:
                    module.sync_mysql(rows, "hotel", fixture.now, initial=False, connection=db)
                else:
                    module.sync_mysql(rows, db)
            yield table, rows, cutoff, discriminator, write

    def test_five_writers_keep_boundary_and_scope_expired_deletion(self):
        for table, rows, cutoff, discriminator, write in self.cases():
            with self.subTest(table=table):
                db = Database(table, rows[0])
                self.addCleanup(db.db.close)
                column = RULES[table][0]
                expired = "2023-10" if isinstance(cutoff, str) else cutoff - timedelta(days=1)
                base = {**rows[0], column: expired}
                db.seed(table, base)
                db.seed(table, {**base, column: cutoff})
                db.seed(table, {**base, "hotel_id": "other", "hotel_name": "其他酒店"})
                db.seed(table, {**base, "source_platform": "其他PMS", discriminator: "其他来源"})
                write(db)
                saved = db.db.execute(f"SELECT hotel_id,source_platform,`{column}` FROM `{table}`").fetchall()
                self.assertNotIn(("hotel", SOURCE, db.value(expired)), saved)
                self.assertIn(("hotel", SOURCE, db.value(cutoff)), saved)
                self.assertIn(("other", SOURCE, db.value(expired)), saved)
                self.assertIn(("hotel", "其他PMS", db.value(expired)), saved)
                write(db)  # 再次写入保持幂等。
                self.assertEqual(len(db.db.execute(f"SELECT * FROM `{table}`").fetchall()), len(saved))

    def test_cleanup_failure_rolls_back_all_five_writers(self):
        for table, rows, cutoff, _, write in self.cases():
            with self.subTest(table=table):
                db = Database(table, rows[0])
                self.addCleanup(db.db.close)
                expired = "2023-10" if isinstance(cutoff, str) else cutoff - timedelta(days=1)
                db.seed(table, {**rows[0], RULES[table][0]: expired})
                before = db.contents(table)
                db.db.execute(f"CREATE TRIGGER reject_cleanup BEFORE DELETE ON `{table}` "
                              "BEGIN SELECT RAISE(ABORT, 'cleanup failed'); END")
                with self.assertRaises(sqlite3.IntegrityError):
                    write(db)
                self.assertEqual(db.contents(table), before)
                if table == rooms.storage.HOURLY:
                    self.assertEqual(db.contents(rooms.storage.FORECAST), [])

    def test_panel_limits_override_inherited_environment(self):
        configured = {"pms_hourly_days": 20, "pms_daily_days": 60, "jl02_daily_days": 500, "pms_monthly_months": 12}
        os.environ["PMS_RETENTION"] = '{"pms_daily_days":1}'
        env = rooms.runner.build_env({"data_retention": configured}, "meituan_pms")
        self.assertTrue(configured.items() <= json.loads(env["PMS_RETENTION"]).items())
        with patch.dict(os.environ, {"PMS_RETENTION": env["PMS_RETENTION"]}):
            for table, expected in (("pms_room_type_hourly_status", date(2026, 10, 9)),
                                    (daily.report.TABLE, date(2026, 8, 30)),
                                    (monthly.report.TABLE, "2025-11"),
                                    (performance.report.TABLE, date(2025, 6, 16)),
                                    (revenue.report.TABLE, date(2026, 8, 30))):
                cursor = MagicMock()
                cleanup_history(cursor, table, "hotel", datetime(2026, 10, 28))
                self.assertEqual(cursor.execute.call_args.args[1], ("hotel", SOURCE, expected))
        defaults = json.loads(rooms.runner.build_env({}, "meituan_pms")["PMS_RETENTION"])
        self.assertEqual(defaults["pms_daily_days"], 400)

    def test_cross_year_leap_day_and_invalid_limits(self):
        cursor = MagicMock()
        for table, snapshot, expected in ((daily.report.TABLE, datetime(2024, 3, 1), date(2023, 1, 27)),
                                          (monthly.report.TABLE, datetime(2027, 1, 1), "2024-02")):
            cleanup_history(cursor, table, "hotel", snapshot)
            self.assertEqual(cursor.execute.call_args.args[1][-1], expected)
        for configured in ('[]', '{"pms_daily_days":0}', '{"pms_daily_days":-1}', 'invalid'):
            with patch.dict(os.environ, {"PMS_RETENTION": configured}):
                cursor.reset_mock()
                with self.assertRaises(ValueError):
                    cleanup_history(cursor, daily.report.TABLE, "hotel", datetime(2026, 10, 28))
                cursor.execute.assert_not_called()
