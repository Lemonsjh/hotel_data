"""汇总报表只使用现有表的列，酒店级指标相加后重算比率。"""
from datetime import date, datetime, timedelta
from decimal import Decimal
import unittest

from meituan_pms.daily_report import collection_dates
from meituan_pms.performance_report import collection_dates as performance_dates
from pms_dual.business import (
    JD01_COLUMNS, JD02_COLUMNS, JL02_COLUMNS, JY01_COLUMNS, JY03_COLUMNS, RS01_COLUMNS, daily_row,
    merge_jl02, merge_jy01, merge_jy03, month_row, performance_from_daily, performance_history_start,
    rezen_booking_row, separate_matching_fees, stamp_orders,
)
from pms_dual.store import TABLES, create_statement


STAMP = datetime(2026, 11, 11, 12, 0, 0)


def jy01(day, rooms, nights, revenue, sold, remaining, hotel="h1"):
    return {
        "hotel_id": hotel, "hotel_name": "测试酒店", "source_platform": "单侧",
        "business_date": day, "dimension_type": "总营业指标", "dimension_name": "总营业指标",
        "room_type_id": None, "room_count": rooms, "room_nights": nights, "room_revenue": Decimal(revenue),
        "occupancy_rate": Decimal("1.00"), "adr": Decimal("1.00"), "revpar": Decimal("1.00"),
        "sold_rooms": sold, "remaining_rooms": remaining, "orders_today": None, "snapshot_time": STAMP,
    }


def jl02(day, nights, revenue, occupancy, adr, revpar):
    values = {"间夜数": nights, "出租率": occupancy, "总房费": revenue, "平均房价": adr, "RevPar": revpar}
    return [{
        "hotel_id": "h1", "hotel_name": "测试酒店", "source_platform": "单侧",
        "business_date": day, "category": "总营业指标", "metric_name": name,
        "value_day": Decimal(value), "value_month": Decimal(value), "value_year": Decimal(value),
        "snapshot_time": STAMP,
    } for name, value in values.items()]


class BusinessMergeTest(unittest.TestCase):
    def test_daily_rows_use_the_existing_columns_and_recalculate_rates(self):
        days = collection_dates(STAMP.date())
        left = [jy01(day, 40, 10, "1000", 10, 30) for day in days]
        right = [jy01(day, 20, 5, "400", 5, 15) for day in days]
        merged = merge_jy01(left, right, "h1", "测试酒店", STAMP)
        self.assertEqual([list(row) for row in merged], [list(JY01_COLUMNS)] * len(days))
        self.assertEqual(merged[0]["room_count"], 60)
        self.assertEqual(merged[0]["sold_rooms"], 15)
        self.assertEqual(merged[0]["remaining_rooms"], 45)
        self.assertEqual(merged[0]["room_revenue"], Decimal("1400.00"))
        self.assertEqual(merged[0]["occupancy_rate"], Decimal("25.00"))
        self.assertEqual(merged[0]["adr"], Decimal("93.33"))
        self.assertEqual(merged[0]["revpar"], Decimal("23.33"))
        self.assertEqual(merged[0]["source_platform"], "PMS（美团+Rezen）")

    def test_daily_merge_rejects_a_missing_day(self):
        days = collection_dates(STAMP.date())
        left = [jy01(day, 40, 10, "1000", 10, 30) for day in days]
        right = [jy01(day, 20, 5, "400", 5, 15) for day in days[:-1]]
        right.append(jy01(days[-1] - timedelta(days=40), 20, 5, "400", 5, 15))
        with self.assertRaisesRegex(ValueError, "日期不一致"):
            merge_jy01(left, right, "h1", "测试酒店", STAMP)

    def test_monthly_sums_only_shared_months_and_keeps_current_month(self):
        left = [month_row("2026-10", {"room_count": 100, "maintain_rooms": 10, "nights": 40, "revenue": "800"},
                          "h1", "测试酒店", STAMP),
                month_row("2026-11", {"room_count": 50, "maintain_rooms": 0, "nights": 20, "revenue": "400"},
                          "h1", "测试酒店", STAMP)]
        right = [month_row("2026-09", {"room_count": 10, "maintain_rooms": 0, "nights": 1, "revenue": "10"},
                           "h1", "测试酒店", STAMP),
                 month_row("2026-11", {"room_count": 30, "maintain_rooms": 5, "nights": 10, "revenue": "100"},
                           "h1", "测试酒店", STAMP)]
        merged = merge_jy03(left, right, "h1", "测试酒店", STAMP)
        self.assertEqual([row["period_month"] for row in merged], ["2026-11"])
        self.assertEqual(list(merged[0]), list(JY03_COLUMNS))
        self.assertEqual(merged[0]["room_count"], 80)
        self.assertEqual(merged[0]["maintain_rooms"], 5)
        self.assertEqual(merged[0]["room_nights"], Decimal("30"))
        self.assertEqual(merged[0]["occupancy_rate"], Decimal("40.00"))

    def test_performance_adds_components_before_recalculating_rates(self):
        days = performance_dates(STAMP.date())
        left, right = [], []
        for day in days:
            left.extend(jl02(day, "10", "1000", "50", "100", "50"))
            right.extend(jl02(day, "5", "500", "25", "100", "25"))
            left.append({**left[-1], "metric_name": "餐饮", "value_day": Decimal("1"),
                         "value_month": Decimal("1"), "value_year": Decimal("1")})
        merged = merge_jl02(left, right, "h1", "测试酒店", STAMP)
        self.assertEqual(len(merged), len(days) * 5)
        self.assertEqual(list(merged[0]), list(JL02_COLUMNS))
        by_name = {row["metric_name"]: row for row in merged if row["business_date"] == days[0]}
        self.assertEqual(set(by_name), {"间夜数", "出租率", "总房费", "平均房价", "RevPar"})
        self.assertEqual(by_name["间夜数"]["value_day"], Decimal("15.0000"))
        self.assertEqual(by_name["总房费"]["value_day"], Decimal("1500.0000"))
        self.assertEqual(by_name["出租率"]["value_day"], Decimal("37.5000"))
        self.assertEqual(by_name["平均房价"]["value_day"], Decimal("100.0000"))
        self.assertEqual(by_name["RevPar"]["value_day"], Decimal("37.5000"))

    def test_zero_revenue_keeps_collected_room_days(self):
        days = performance_dates(STAMP.date())
        inventory = {date(2026, 11, 10): {"nights": 0, "revenue": 0, "rentable": 10, "room_count": 10}}
        rezen = performance_from_daily(inventory, "h1", "测试酒店", STAMP, zero_before=date(2026, 11, 10))
        stored = next(row for row in rezen if row["business_date"] == date(2026, 11, 10))
        self.assertEqual(stored["components"]["day"], (Decimal(0), Decimal(0), Decimal(10), Decimal(10)))
        meituan = []
        for day in days:
            meituan.extend(jl02(day, "1", "100", "50", "100", "50"))
        merged = merge_jl02(meituan, rezen, "h1", "测试酒店", STAMP)
        by_name = {row["metric_name"]: row for row in merged if row["business_date"] == date(2026, 11, 10)}
        self.assertEqual(by_name["间夜数"]["value_day"], Decimal("1.0000"))
        self.assertEqual(by_name["总房费"]["value_day"], Decimal("100.0000"))
        self.assertEqual(by_name["RevPar"]["value_day"], Decimal("8.3333"))
        self.assertEqual(by_name["出租率"]["value_day"], Decimal("8.3333"))
        self.assertEqual(by_name["平均房价"]["value_day"], Decimal("100.0000"))
        self.assertNotIn("components", merged[0])

    def test_rezen_performance_requires_every_day_in_the_period(self):
        start, end = date(2025, 1, 1), date(2026, 11, 10)
        days = {}
        cursor = start
        while cursor <= end:
            days[cursor] = {"nights": Decimal(1), "revenue": Decimal(10), "rentable": 2, "room_count": 2}
            cursor += timedelta(days=1)
        rows = performance_from_daily(days, "h1", "测试酒店", STAMP)
        yesterday = {row["metric_name"]: row for row in rows if row["business_date"] == end}
        self.assertEqual(yesterday["间夜数"]["value_day"], Decimal("1.0000"))
        self.assertEqual(yesterday["间夜数"]["value_month"], Decimal("10.0000"))
        self.assertEqual(yesterday["间夜数"]["value_year"], Decimal("314.0000"))
        del days[date(2026, 11, 1)]
        with self.assertRaisesRegex(ValueError, "日期不完整"):
            performance_from_daily(days, "h1", "测试酒店", STAMP)

    def test_zero_months_do_not_require_daily_rows(self):
        months = {}
        cursor = date(2025, 1, 1)
        while cursor <= date(2026, 11, 1):
            months[cursor.strftime("%Y-%m")] = {"room_count": 0, "maintain_rooms": 0, "nights": 0, "revenue": 0}
            cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)
        months["2026-09"] = {"room_count": 10, "maintain_rooms": 0, "nights": 1, "revenue": 1}
        self.assertEqual(performance_history_start(months, date(2025, 1, 1), date(2026, 11, 10)), date(2026, 9, 1))
        days = {}
        cursor = date(2026, 9, 1)
        while cursor <= date(2026, 11, 10):
            days[cursor] = {"nights": Decimal(1), "revenue": Decimal(10), "rentable": 2, "room_count": 2}
            cursor += timedelta(days=1)
        rows = performance_from_daily(days, "h1", "测试酒店", STAMP, zero_before=date(2026, 9, 1))
        yesterday = {row["metric_name"]: row for row in rows if row["business_date"] == date(2026, 11, 10)}
        earlier = {row["metric_name"]: row for row in rows if row["business_date"] == date(2025, 11, 10)}
        self.assertEqual(yesterday["间夜数"]["value_year"], Decimal("71.0000"))
        self.assertEqual(earlier["间夜数"]["value_day"], Decimal("0.0000"))
        self.assertEqual(earlier["间夜数"]["value_year"], Decimal("0.0000"))

    def test_order_numbers_stay_original_and_columns_match_the_existing_tables(self):
        booking = {
            "hotel_name": "测试酒店", "hotel_id": "h1", "source_platform": "单侧", "order_id": "100",
            "booking_time": STAMP, "contact": "甲", "guest_source": "上门", "member_level": None,
            "arrival_time": STAMP, "departure_time": STAMP, "room_type_name": "溪眠", "room_count": 1,
            "price_type": None, "room_price": Decimal("100.00"), "prepayment": Decimal("0.00"),
            "guarantee_method": None, "booking_status": "预订中", "hold_time": None, "remarks": None,
            "operator_name": None, "snapshot_time": STAMP,
        }
        guest = {key: None for key in JD02_COLUMNS if key != "room_type_id"}
        guest.update(order_id="100", hotel_id="h1", hotel_name="测试酒店", business_date=date(2026, 11, 10),
                     arrival_time=STAMP, departure_time=STAMP, snapshot_time=STAMP, main_checkin_person=1,
                     room_price=Decimal("100.00"))
        left = stamp_orders([booking, {**booking, "room_type_name": "山语"}], "h1", "测试酒店", STAMP, JD01_COLUMNS, "pms_meituan")
        right = stamp_orders([guest], "h1", "测试酒店", STAMP, JD02_COLUMNS, "pms_rezen")
        self.assertEqual([row["order_id"] for row in left], ["100", "100"])
        self.assertEqual(right[0]["order_id"], "100")
        self.assertEqual(set(left[0]) - {"mapping_platform"}, set(JD01_COLUMNS))
        self.assertEqual(set(right[0]) - {"mapping_platform"}, set(JD02_COLUMNS))
        with self.assertRaisesRegex(ValueError, "记录重复"):
            stamp_orders([booking, {**booking}], "h1", "测试酒店", STAMP, JD01_COLUMNS, "pms_meituan")
        companions = stamp_orders(
            [guest, {**guest, "customer_id": "2", "main_checkin_person": 0}],
            "h1", "测试酒店", STAMP, JD02_COLUMNS, "pms_rezen")
        self.assertEqual([row["order_id"] for row in companions], ["100", "100"])
        with self.assertRaisesRegex(ValueError, "记录重复"):
            stamp_orders([guest, {**guest}], "h1", "测试酒店", STAMP, JD02_COLUMNS, "pms_rezen")
        fee = {key: None for key in RS01_COLUMNS}
        fee.update(order_id="100", business_date=date(2026, 11, 10), room_no="101", charge_subject="房费",
                   room_fee=Decimal("10.00"), hotel_id="h1", hotel_name="测试酒店", snapshot_time=STAMP)
        meituan = stamp_orders([fee], "h1", "测试酒店", STAMP, RS01_COLUMNS, "pms_meituan")
        rezen = stamp_orders([{**fee, "room_fee": Decimal("20.00")}], "h1", "测试酒店", STAMP, RS01_COLUMNS, "pms_rezen")
        separated = separate_matching_fees([*meituan, *rezen])
        self.assertEqual([row["order_id"] for row in separated], ["100", "100"])
        self.assertEqual([row["source_payment_id"] for row in separated], ["pms_meituan", "pms_rezen"])
        with self.assertRaisesRegex(ValueError, "房费明细重复"):
            separate_matching_fees(stamp_orders(
                [fee, {**fee, "room_fee": Decimal("2.00")}], "h1", "测试酒店", STAMP, RS01_COLUMNS, "pms_meituan"))

    def test_rezen_booking_rejects_an_overlong_field_without_echoing_it(self):
        account = {"acctStus": "R", "acctNum": "9", "rmNums": 1, "arrDt": "2026-11-11", "gstarrTm": "12:00",
                   "dptDt": "2026-11-12", "gstdptTm": "12:00", "rtAmt": "100", "bookGuestNm": "甲" * 101,
                   "rmTypDrpt": "溪眠"}
        with self.assertRaisesRegex(ValueError, "超过字段长度") as caught:
            rezen_booking_row(account, "h1", "测试酒店", STAMP, date(2026, 10, 13), date(2026, 11, 11))
        self.assertNotIn("甲", str(caught.exception))

    def test_prepayment_keeps_a_negative_amount_and_a_blank_stays_empty(self):
        account = {"acctStus": "R", "acctNum": "9", "rmNums": 1, "arrDt": "2026-11-11", "gstarrTm": "12:00",
                   "dptDt": "2026-11-12", "gstdptTm": "12:00", "rtAmt": "100", "paymentAmt": "-12.5",
                   "bookGuestNm": "甲", "rmTypDrpt": "溪眠"}
        row = rezen_booking_row(account, "h1", "测试酒店", STAMP, date(2026, 10, 13), date(2026, 11, 11))
        self.assertEqual(row["prepayment"], Decimal("-12.50"))
        self.assertEqual(row["room_price"], Decimal("100.00"))
        account.pop("paymentAmt")
        self.assertIsNone(rezen_booking_row(
            account, "h1", "测试酒店", STAMP, date(2026, 10, 13), date(2026, 11, 11))["prepayment"])

    def test_daily_builder_matches_the_table_columns(self):
        row = daily_row(date(2026, 11, 10), {"room_count": 90, "rentable": 69, "nights": Decimal(28),
                                             "revenue": Decimal("2685.72")}, "h1", "测试酒店", STAMP)
        self.assertEqual(list(row), list(JY01_COLUMNS))
        self.assertEqual(row["sold_rooms"], 28)
        self.assertEqual(row["remaining_rooms"], 41)

    def test_create_statements_keep_the_existing_table_names(self):
        for table in TABLES:
            statement = create_statement(table)
            self.assertIn(f"CREATE TABLE IF NOT EXISTS `{table}`", statement)
            self.assertNotIn("ALTER TABLE", statement)
            self.assertNotIn("DROP COLUMN", statement)
