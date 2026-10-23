from __future__ import annotations

import importlib.util
import os
import re
import sqlite3
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "宝寓PMS数据采集代码" / "bypms_room_status_data.py"


def load_target():
    os.environ.setdefault("HOTEL_ID", "test-hotel")
    spec = importlib.util.spec_from_file_location("test_bypms_room_status_target", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_state_request_is_today_to_next_twenty_days():
    target = load_target()
    assert target.state_form(date(2026, 8, 20)) == {
        "date": "2026-08-20,2026-09-09",
        "tagId": "0",
        "tbl": "monthA",
    }


def test_room_type_inventory_uses_room_master_and_excludes_checkout_date():
    target = load_target()
    payload = {"data": {"data": [
        {"id": 1, "state": "S", "roomId": 11, "checkIn": "2026-08-20", "checkOut": "2026-08-21", "contractPrice": 120},
        {"id": 2, "state": "P", "roomId": 12, "checkIn": "2026-08-21", "checkOut": "2026-08-22"},
        {"id": 3, "state": "L", "roomId": 13, "checkIn": "2026-08-20", "checkOut": "2026-08-22", "tag": "fix"},
    ]}}
    master = {"typeVos": [{"id": 21, "name": "大床房"}], "vos": [
        {"id": 11, "type": 21}, {"id": 12, "type": 21}, {"id": 13, "type": 21},
    ]}

    row = target.room_type_inventory_rows(payload, master, date(2026, 8, 20), datetime(2026, 8, 20, 10))[0]

    assert row["total_rooms"] == 3
    assert row["sold_rooms"] == 1
    assert row["arrival_rooms"] == 1
    assert row["repair_rooms"] == 1
    assert row["saleable_rooms"] == 2
    assert row["remaining_saleable_rooms"] == 1
    assert row["occupancy_rate"] == 50
    assert row["room_revenue"] == 120
    assert row["adr"] == 120
    assert row["revpar"] == 40


def test_multi_night_order_uses_current_night_price_not_contract_total():
    target = load_target()
    payload = {"data": {"data": [
        {"id": 1, "state": "S", "roomId": 11, "checkIn": "2026-08-19", "checkOut": "2026-08-22", "amount": 3, "contractAmount": 1, "contractPrice": 450, "price": 450},
        {"id": 2, "state": "P", "roomId": 12, "checkIn": "2026-08-20", "checkOut": "2026-08-21", "contractPrice": 100},
    ]}}
    master = {"typeVos": [{"id": 21, "name": "大床房"}], "vos": [{"id": 11, "type": 21}, {"id": 12, "type": 21}]}

    row = target.room_type_inventory_rows(payload, master, date(2026, 8, 20), datetime(2026, 8, 20, 10))[0]

    assert row["room_revenue"] == 250
    assert row["adr"] == 125
    assert row["revpar"] == 125


def test_inventory_financial_metrics_are_limited_to_two_decimal_places():
    target = load_target()
    payload = {"data": {"data": [
        {"id": 1, "state": "S", "roomId": 11, "checkIn": "2026-08-19", "checkOut": "2026-08-22", "amount": 3, "contractPrice": 253},
    ]}}
    master = {"typeVos": [{"id": 21, "name": "大床房"}], "vos": [{"id": 11, "type": 21}]}

    row = target.room_type_inventory_rows(payload, master, date(2026, 8, 20), datetime(2026, 8, 20, 10))[0]

    assert str(row["room_revenue"]) == "84.33"
    assert str(row["adr"]) == "84.33"
    assert str(row["revpar"]) == "84.33"


def test_inventory_rows_are_adapted_to_the_standard_pms_hourly_contract():
    target = load_target()
    source = {
        "hotel_id": "test-hotel", "hotel_name": "测试酒店", "business_date": date(2026, 8, 20),
        "snapshot_time": datetime(2026, 8, 20, 10), "snapshot_hour": datetime(2026, 8, 20, 10),
        "bypms_room_type_id": "21", "room_type_name": "大床房", "total_rooms": 3,
        "remaining_saleable_rooms": 1, "sold_rooms": 1,
    }

    row = target.standard_inventory_rows([source])[0]

    assert row["source_platform"] == "PMS（宝寓）"
    assert row["stay_date"] == source["business_date"]
    assert row["pms_room_type_id"] == "21"
    assert row["available_rooms"] == 1
    assert row["occupied_rooms"] == 1
    assert row["overbooking_rooms"] == 0

    forecast_row = target.standard_forecast_rows([source])[0]
    assert forecast_row["stay_date"] == source["business_date"]
    assert forecast_row["room_revenue"] is None


class ByPmsRoomStatusContractTests(unittest.TestCase):
    def test_missing_room_master_is_a_safe_degradation_not_a_parser_error(self):
        target = load_target()
        self.assertIsNone(target.extract_room_master("<html><title>宝寓官网</title></html>"))

    def test_var_rooms_declaration_is_recognized(self):
        target = load_target()
        master = target.extract_room_master('var _ROOMS = {"typeVos":[{"id":1,"name":"大床房"}],"vos":[]};')
        self.assertEqual(master["typeVos"][0]["name"], "大床房")

    def test_nested_room_master_api_payload_is_supported(self):
        target = load_target()
        result = target.room_master_from_value({"data": {"typeVos": [], "vos": [{"id": 1, "type": 2}]}})
        self.assertEqual(result["vos"][0]["id"], 1)

    def test_standard_hourly_fields_keep_by_pms_identity_and_counts(self):
        target = load_target()
        source = {
            "hotel_id": "test-hotel", "hotel_name": "测试酒店", "business_date": date(2026, 8, 20),
            "snapshot_time": datetime(2026, 8, 20, 10), "snapshot_hour": datetime(2026, 8, 20, 10),
            "bypms_room_type_id": "21", "room_type_name": "大床房", "total_rooms": 3,
            "remaining_saleable_rooms": 1, "sold_rooms": 1,
        }

        row = target.standard_inventory_rows([source])[0]

        self.assertEqual(row["source_platform"], "PMS（宝寓）")
        self.assertEqual(row["stay_date"], source["business_date"])
        self.assertEqual(row["pms_room_type_id"], "21")
        self.assertEqual(row["available_rooms"], 1)
        self.assertEqual(row["occupied_rooms"], 1)

    def test_inventory_sync_writes_only_standard_pms_tables(self):
        target = load_target()

        class Cursor:
            def __init__(self):
                self.statements = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, _params=()):
                self.statements.append(sql)

            def executemany(self, sql, _rows):
                self.statements.append(sql)

        class Connection:
            def __init__(self):
                self.cursor_instance = Cursor()

            def cursor(self):
                return self.cursor_instance

            def commit(self):
                pass

            def rollback(self):
                pass

        source = {
            "hotel_id": "test-hotel", "hotel_name": "测试酒店", "business_date": date(2026, 8, 20),
            "snapshot_time": datetime(2026, 8, 20, 10), "snapshot_hour": datetime(2026, 8, 20, 10),
            "bypms_room_type_id": "21", "room_type_name": "大床房", "total_rooms": 3,
            "remaining_saleable_rooms": 1, "sold_rooms": 1,
        }
        connection = Connection()

        target.sync_mysql([source], source["snapshot_hour"], connection=connection)

        sql = "\n".join(connection.cursor_instance.statements)
        self.assertIn("pms_room_type_forecast", sql)
        self.assertIn("pms_room_type_hourly_status", sql)
        self.assertNotIn("bypms_room_type_hourly_status", sql)


class ByPmsLatestForecastTests(unittest.TestCase):
    def setUp(self):
        self.target = load_target()
        self.hour = datetime(2026, 10, 23, 10)
        self.source = {
            "hotel_id": self.target.HOTEL_ID, "hotel_name": "测试酒店",
            "business_date": self.hour.date(), "snapshot_time": self.hour, "snapshot_hour": self.hour,
            "bypms_room_type_id": "21", "room_type_name": "大床房", "total_rooms": 3,
            "remaining_saleable_rooms": 2, "sold_rooms": 1,
        }
        self.forecast = self.target.standard_forecast_rows([self.source])[0]
        self.hourly = self.target.standard_inventory_rows([self.source])[0]
        self.database = sqlite3.connect(":memory:")
        self.addCleanup(self.database.close)
        for table, row, keys in (
            (self.target.FORECAST_TABLE_NAME, self.forecast, "hotel_id,stay_date,pms_room_type_id"),
            (self.target.STANDARD_ROOM_TYPE_TABLE_NAME, self.hourly,
             "hotel_id,source_platform,snapshot_hour,pms_room_type_id"),
        ):
            columns = ",".join(f"`{name}` TEXT" for name in row)
            self.database.execute(f"CREATE TABLE `{table}` ({columns}, PRIMARY KEY ({keys}))")
        self.cursor = MagicMock()
        self.cursor.__enter__.return_value = self.cursor
        self.cursor.execute.side_effect = lambda sql, params: self.database.execute(
            sql.replace("%s", "?"), [None if value is None else str(value) for value in params])
        self.cursor.executemany.side_effect = self.insert_many
        self.connection = MagicMock()
        self.connection.cursor.return_value = self.cursor
        self.connection.commit.side_effect = self.database.commit
        self.connection.rollback.side_effect = self.database.rollback

    def insert_many(self, sql, rows):
        sql = re.sub(r"%\((\w+)\)s", r":\1", sql)
        return self.database.executemany(sql, [
            {key: None if value is None else str(value) for key, value in row.items()} for row in rows
        ])

    def seed(self, table, row):
        columns = ",".join(f"`{name}`" for name in row)
        self.database.execute(f"INSERT INTO `{table}` ({columns}) VALUES ({','.join('?' for _ in row)})",
                              [None if value is None else str(value) for value in row.values()])
        self.database.commit()

    def contents(self, table):
        return self.database.execute(f"SELECT * FROM `{table}` ORDER BY hotel_id,stay_date,pms_room_type_id").fetchall()

    def test_forecast_replaces_all_old_dates_only_for_current_hotel_and_provider(self):
        table = self.target.FORECAST_TABLE_NAME
        for day in ("2026-10-22", "2026-10-23", "2026-10-24"):
            self.seed(table, {**self.forecast, "stay_date": day, "pms_room_type_id": "obsolete"})
        others = [{**self.forecast, "hotel_id": "other-hotel"},
                  {**self.forecast, "source_platform": "PMS（Foxhis）", "pms_room_type_id": "foxhis-type"}]
        for row in others:
            self.seed(table, row)
        self.target.sync_mysql([self.source], self.hour, connection=self.connection)
        self.target.sync_mysql([self.source], self.hour, connection=self.connection)
        rows = self.database.execute(f"SELECT hotel_id,source_platform,stay_date,pms_room_type_id FROM `{table}`").fetchall()
        self.assertCountEqual(rows, [(row["hotel_id"], row["source_platform"], str(row["stay_date"]), row["pms_room_type_id"])
                                    for row in [self.forecast, *others]])

    def test_hourly_history_is_retained_and_only_current_hour_is_replaced(self):
        table = self.target.STANDARD_ROOM_TYPE_TABLE_NAME
        for day, hour in (("2026-10-22", "2026-10-22 10:00:00"),
                          ("2026-10-23", "2026-10-23 09:00:00"), ("2026-10-23", str(self.hour))):
            self.seed(table, {**self.hourly, "stay_date": day, "snapshot_hour": hour, "pms_room_type_id": "obsolete"})
        self.target.sync_mysql([self.source], self.hour, connection=self.connection)
        rows = self.database.execute(f"SELECT snapshot_hour,pms_room_type_id FROM `{table}`").fetchall()
        self.assertCountEqual(rows, [("2026-10-22 10:00:00", "obsolete"),
                                     ("2026-10-23 09:00:00", "obsolete"), (str(self.hour), "21")])

    def test_empty_inventory_keeps_previous_forecast(self):
        self.seed(self.target.FORECAST_TABLE_NAME, self.forecast)
        before = self.contents(self.target.FORECAST_TABLE_NAME)
        self.target.sync_mysql([], self.hour, connection=self.connection)
        self.assertEqual(self.contents(self.target.FORECAST_TABLE_NAME), before)
        self.cursor.execute.assert_not_called()

    def test_insert_failure_rolls_back_both_forecast_and_hourly_changes(self):
        for table, row in ((self.target.FORECAST_TABLE_NAME, self.forecast),
                           (self.target.STANDARD_ROOM_TYPE_TABLE_NAME, self.hourly)):
            self.seed(table, {**row, "pms_room_type_id": "obsolete"})
        before = {table: self.contents(table) for table in (self.target.FORECAST_TABLE_NAME, self.target.STANDARD_ROOM_TYPE_TABLE_NAME)}
        def fail_forecast(sql, rows):
            if f"INSERT INTO `{self.target.FORECAST_TABLE_NAME}`" in sql:
                raise RuntimeError("simulated insert failure")
            return self.insert_many(sql, rows)
        self.cursor.executemany.side_effect = fail_forecast
        with self.assertRaisesRegex(RuntimeError, "simulated insert failure"):
            self.target.sync_mysql([self.source], self.hour, connection=self.connection)
        self.assertEqual({table: self.contents(table) for table in before}, before)
        self.connection.rollback.assert_called_once()
        self.connection.commit.assert_not_called()
