"""RS01 字段映射、流水稳定键及退房月替换事务回归测试。"""
import sys
import sqlite3
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "OTA采集服务/foxhis_pms"))
import foxhis_room_revenue_data as rs
from foxhis_common import Config, FoxhisError, SOURCE_PLATFORM
from .test_realtime_forecast import DatabaseAdapter


class RS01Tests(unittest.TestCase):
    def setUp(self):
        self.stamp = datetime(2026, 10, 19, 14, 30)
        self.start, self.end = rs.month_window(self.stamp.date())
        self.config = Config(hotel_id="TEST", hotel_name="测试酒店")
        self.raw_order = {"folio_no": "F1", "order_origin": "current", "room_no": "101", "room_type_code": "A",
                          "guest_name": "测试客人", "market_code": "OTA", "price_type": "OTA",
                          "checkin_time": "20260930 14:00:00", "checkout_time": "20261002 12:00:00"}
        self.orders = rs.order_map([self.raw_order], self.start, self.end, self.stamp)
        self.db = DatabaseAdapter()
        columns = ",".join(f"`{name}` TEXT" for name in rs.INSERT_COLUMNS)
        self.db.db.execute(f"CREATE TABLE rs01_room_revenue_daily ({columns}, UNIQUE(hotel_id,order_id,business_date,room_no,charge_subject))")
        self.db.db.commit()
        self.connection_patch = patch.object(rs, "mysql_connection", return_value=self.db)
        self.connection_patch.start()

    def tearDown(self):
        self.connection_patch.stop()
        self.db.db.close()

    def record(self, **changes):
        return {"folio_no": "F1", "line_no": "1", "business_date": "20260930", "room_no": "101",
                "charge_code": "1000", "charge_subject": "房费", "charge_tail": "01", "amount": "120.00",
                "quantity": "1", "operator_code": "OP", "operator_name": "操作员", **changes}

    def build(self, records):
        return rs.build_rows(records, self.orders, {"A": "大床房", "B": "双床房"}, {"101": "A", "201": "B"}, {"OTA": "携程"}, self.config, self.stamp)

    def seed(self, row):
        sql = "INSERT INTO rs01_room_revenue_daily (" + ",".join(rs.INSERT_COLUMNS) + ") VALUES (" + ",".join(["%s"] * len(rs.INSERT_COLUMNS)) + ")"
        self.db.executemany(sql, [tuple(row[name] for name in rs.INSERT_COLUMNS)])
        self.db.commit()

    def contents(self):
        return self.db.db.execute("SELECT hotel_id,source_platform,order_id,business_date,checkout_time,room_fee FROM rs01_room_revenue_daily ORDER BY hotel_id,order_id,business_date").fetchall()

    def test_calendar_month_boundary_including_december(self):
        self.assertEqual(rs.month_window(date(2026, 12, 31)), (date(2026, 12, 1), date(2027, 1, 1)))
        self.assertEqual(rs.month_window(date(2024, 2, 29)), (date(2024, 2, 1), date(2024, 3, 1)))

    def test_cross_month_fee_dates_and_checkout_metadata(self):
        rows, summary = self.build([self.record()])
        row = rows[0]
        self.assertEqual(row["business_date"], date(2026, 9, 30))
        self.assertEqual(row["checkout_time"], datetime(2026, 10, 2, 12))
        self.assertEqual(row["guest_name"], "测试客人")
        self.assertEqual(row["customer_source"], "携程")
        self.assertEqual(row["room_type_name"], "大床房")
        self.assertEqual(row["room_daily_price"], Decimal("120.00"))
        self.assertIsNone(row["channel_unit_id"])
        self.assertEqual(summary["checkout_orders"], 1)

    def test_multiple_postings_under_one_order_have_distinct_stable_keys(self):
        rows, _ = self.build([self.record(), self.record(line_no="2", amount="80")])
        self.assertEqual({r["order_id"] for r in rows}, {"foxhis:F1:1", "foxhis:F1:2"})
        rs.sync_rows(rows, self.config, self.start, self.end)
        self.assertEqual(len(self.contents()), 2)

    def test_transferred_ledger_is_deduplicated_but_conflict_fails(self):
        rows, summary = self.build([self.record(), self.record(ledger_origin="history")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(summary["deduplicated_rows"], 1)
        with self.assertRaises(FoxhisError):
            self.build([self.record(), self.record(amount="121")])

    def test_negative_adjustments_free_rooms_and_zero_placeholders(self):
        rows, summary = self.build([self.record(), self.record(line_no="2", amount="-20", quantity="0"),
                                    self.record(line_no="3", amount="0", quantity="1"),
                                    self.record(line_no="4", amount="0", quantity="0")])
        self.assertEqual(sum(r["room_fee"] for r in rows), Decimal("100.00"))
        self.assertEqual(len(rows), 3)
        self.assertEqual(summary["zero_placeholders_skipped"], 1)
        self.assertIsNone(rows[1]["room_daily_price"])

    def test_room_move_uses_posting_room_type(self):
        rows, _ = self.build([self.record(room_no="201")])
        self.assertEqual(rows[0]["room_type_name"], "双床房")

    def test_historical_checkout_type_is_not_overwritten_by_current_room_classification(self):
        orders = {"F1": {**self.orders["F1"], "room_type_code": "B"}}
        rows, _ = rs.build_rows([self.record()], orders, {"A": "现房型", "B": "历史房型"},
                               {"101": "A"}, {}, self.config, self.stamp)
        self.assertEqual(rows[0]["room_type_name"], "历史房型")

    def test_invalid_money_unknown_order_and_unknown_room_fail(self):
        for changes in ({"amount": "NaN"}, {"amount": ""}, {"quantity": "Infinity"}, {"folio_no": "UNKNOWN"},
                        {"line_no": ""}, {"room_no": "UNKNOWN"}, {"business_date": "bad"}):
            with self.assertRaises(FoxhisError):
                self.build([self.record(**changes)])

    def test_historical_order_name_is_preferred_without_double_join(self):
        orders = rs.order_map([self.raw_order, {**self.raw_order, "order_origin": "history", "guest_name": "历史姓名"}], self.start, self.end, self.stamp)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders["F1"]["guest_name"], "历史姓名")
        with self.assertRaises(FoxhisError):
            rs.order_map([self.raw_order, {**self.raw_order, "room_no": "201"}], self.start, self.end, self.stamp)

    def test_checkout_outside_month_or_in_future_fails(self):
        for checkout in ("20260930 12:00:00", "20261101 00:00:00", "20261030 12:00:00", "bad"):
            with self.assertRaises(FoxhisError):
                rs.order_map([{**self.raw_order, "checkout_time": checkout}], self.start, self.end, self.stamp)

    def test_replacement_preserves_other_scopes_and_removes_stale_current_month(self):
        rows, _ = self.build([self.record()]); row = rows[0]
        self.seed({**row, "order_id": "STALE"})
        self.seed({**row, "order_id": "OLD_MONTH", "checkout_time": datetime(2026, 9, 20, 12)})
        self.seed({**row, "hotel_id": "OTHER"})
        self.seed({**row, "source_platform": "PMS（宝寓）", "order_id": "BYPMS"})
        rs.sync_rows(rows, self.config, self.start, self.end)
        rs.sync_rows(rows, self.config, self.start, self.end)
        result = self.contents()
        self.assertEqual(len(result), 4)
        self.assertNotIn("STALE", {r[2] for r in result})
        self.assertIn("OLD_MONTH", {r[2] for r in result})

    def test_foreign_source_key_conflict_rolls_back_month_deletion(self):
        rows, _ = self.build([self.record()]); row = rows[0]
        self.seed({**row, "order_id": "STALE"})
        self.seed({**row, "source_platform": "PMS（宝寓）"})
        before = self.contents()
        with self.assertRaises(sqlite3.IntegrityError):
            rs.sync_rows(rows, self.config, self.start, self.end)
        self.assertEqual(self.contents(), before)

    def test_empty_mixed_or_older_batch_cannot_delete(self):
        rows, _ = self.build([self.record()]); row = rows[0]
        self.seed({**row, "snapshot_time": self.stamp + timedelta(minutes=1)})
        before = self.contents()
        for invalid in ([], [{**row, "hotel_id": "OTHER"}], [row, row], rows):
            with self.assertRaises(FoxhisError):
                rs.sync_rows(invalid, self.config, self.start, self.end)
        self.assertEqual(self.contents(), before)

    def test_dry_run_never_writes_mysql(self):
        with patch.object(rs, "fetch_rows", return_value=([], {})), patch.object(rs, "sync_rows", side_effect=AssertionError("不应写入")), patch.object(rs, "save_output"), patch.object(sys, "argv", ["rs01", "--dry-run"]):
            summary = rs.collect(self.config, self.stamp.date(), self.stamp)
        self.assertTrue(summary["dry_run"])
        self.assertEqual(summary["written"], 0)

    def test_unassigned_group_charge_preserves_revenue_without_guessing_room(self):
        orders = {"F1": {**self.orders["F1"], "account_class": "G", "room_no": "", "room_type_code": ""}}
        rows, summary = rs.build_rows([self.record(room_no="", charge_code="1060", amount="2000")],
                                      orders, {"A": "大床房"}, {}, {}, self.config, self.stamp)
        self.assertEqual((rows[0]["room_no"], rows[0]["room_type_name"], rows[0]["room_fee"]), ("", None, Decimal("2000")))
        self.assertEqual(summary["unassigned_group_postings"], 1)
        rs.sync_rows(rows, self.config, self.start, self.end)
        self.assertEqual(len(self.contents()), 1)

    def test_roomless_normal_account_and_unrecognized_group_room_still_fail(self):
        for changes, room in [({"account_class": "F", "room_no": "", "room_type_code": ""}, ""),
                              ({"account_class": "G", "room_no": "", "room_type_code": ""}, "UNKNOWN"),
                              ({"account_class": "G", "room_no": "", "room_type_code": "A"}, "")]:
            orders = {"F1": {**self.orders["F1"], **changes}}
            with self.assertRaises(FoxhisError):
                rs.build_rows([self.record(room_no=room)], orders, {"A": "大床房"}, {}, {}, self.config, self.stamp)



if __name__ == "__main__":
    unittest.main(verbosity=2)
