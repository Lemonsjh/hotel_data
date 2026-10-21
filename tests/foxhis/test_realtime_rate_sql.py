"""实时执行价的同日入住及应离未离价格段回归，不访问真实源库。"""
import sqlite3
import unittest
from datetime import datetime
from decimal import Decimal

from . import test_realtime_forecast as fixtures
import foxhis_common as common
import foxhis_room_status_data as status
from foxhis_sql import REALTIME_ROOM_RATE_QUERY


class RealtimeRateSQLTests(unittest.TestCase):
    rooms = fixtures.RealtimeForecastTests.rooms
    forecasts = fixtures.RealtimeForecastTests.forecasts

    def setUp(self):
        self.config = common.Config(hotel_id="HOTEL_A", hotel_name="测试酒店")
        self.stamp = datetime(2026, 10, 19, 0, 15)

    def source_rate_query(self, begin, end, arrival, departure, master_departure="20261020 14:00:00", extra_segments=()):
        # 执行真实查询分支；仅将ASE日期格式化函数适配为SQLite，日期仍按原生112格式比较。
        with sqlite3.connect(":memory:") as source:
            source.row_factory = sqlite3.Row
            source.create_function("convert", 3, lambda kind, value, style: value[:8] if value else None)
            source.executescript("""
                CREATE TABLE rmsta(roomno TEXT,type TEXT,ocsta TEXT);
                CREATE TABLE master(accnt TEXT,roomno TEXT,sta TEXT,rmnum TEXT,share TEXT,dep TEXT);
                CREATE TABLE rsvsrc(id TEXT,accnt TEXT,quantity TEXT,rate TEXT,begin_ TEXT,end_ TEXT,arr TEXT,dep TEXT);
                INSERT INTO rmsta VALUES ('103','A','O');
            """)
            source.execute("INSERT INTO master VALUES ('F1','103','I','1','F',?)", (master_departure,))
            source.execute("INSERT INTO rsvsrc VALUES ('0','F1','1','58.64',?,?,?,?)", (begin, end, arrival, departure))
            source.executemany("INSERT INTO rsvsrc VALUES (?,?,?,?,?,?,?,?)", extra_segments)
            return [dict(row) for row in source.execute(REALTIME_ROOM_RATE_QUERY.format(business_date="20261019"))]

    def test_due_out_occupied_room_uses_its_final_night_segment(self):
        departure = "20261019 14:00:00"
        records = self.source_rate_query("20261018", "20261019", "20261018 18:00:00", departure, master_departure=departure)
        rows = self.forecasts()
        status.apply_realtime_revenue(rows, self.rooms(), records)
        self.assertEqual(rows[0]["room_revenue"], Decimal("58.64"))
        self.assertEqual(rows[0]["occupied_rooms"], 1)

    def test_due_out_fallback_requires_matching_departure_and_valid_last_night(self):
        for end, segment_dep, master_dep in (
            ("20261018", "20261019 14:00:00", "20261019 14:00:00"),
            ("20261019", "20261019 12:00:00", "20261019 14:00:00"),
            ("20261019", "20261019 14:00:00", "20261020 14:00:00"),
            ("20261019", "20261019 14:00:00", None),
        ):
            with self.subTest(end=end, segment_dep=segment_dep, master_dep=master_dep):
                rows = self.source_rate_query("20261017", end, "20261017 18:00:00", segment_dep, master_departure=master_dep)
                self.assertIsNone(rows[0]["rate_segment_id"])

    def test_due_out_fallback_does_not_compete_with_a_current_segment(self):
        departure = "20261019 14:00:00"
        for extra in (
            ("1", "F1", "1", "88.00", "20261019", "20261020", "20261019 00:00:00", "20261020 14:00:00"),
            ("1", "F1", "1", "88.00", "20261019", "20261019", "20261019 01:00:00", departure),
        ):
            with self.subTest(extra=extra):
                records = self.source_rate_query("20261018", "20261019", "20261018 18:00:00", departure, master_departure=departure, extra_segments=[extra])
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["rate_segment_id"], "1")

    def test_same_day_stay_uses_its_verified_zero_length_date_segment(self):
        records = self.source_rate_query("20261019", "20261019", "20261019 01:29:41", "20261019 14:00:00")
        rows = self.forecasts()
        status.apply_realtime_revenue(rows, self.rooms(), records)
        self.assertEqual(rows[0]["room_revenue"], Decimal("58.64"))
        self.assertEqual(records[0]["rate_segment_id"], "0")

    def test_same_day_exception_does_not_include_invalid_or_other_day_segments(self):
        for begin, end, arrival, departure in (
            ("20261018", "20261019", "20261018 12:00:00", "20261019 14:00:00"),
            ("20261018", "20261018", "20261018 01:00:00", "20261018 14:00:00"),
            ("20261020", "20261020", "20261020 01:00:00", "20261020 14:00:00"),
            ("20261019", "20261019", "20261019 14:00:00", "20261019 14:00:00"),
            ("20261019", "20261019", "20261019 14:00:00", "20261019 01:00:00"),
            ("20261019", "20261019", "20261019 01:00:00", "20261020 14:00:00"),
            ("20261019", "20261019", None, None),
        ):
            with self.subTest(begin=begin, end=end, arrival=arrival, departure=departure):
                records = self.source_rate_query(begin, end, arrival, departure)
                self.assertIsNone(records[0]["rate_segment_id"])
                with self.assertRaises(common.FoxhisError):
                    status.apply_realtime_revenue(self.forecasts(), self.rooms(), records)
        records = self.source_rate_query("20261018", "20261020", "20261018 12:00:00", "20261020 14:00:00")
        self.assertEqual(records[0]["rate_segment_id"], "0")
