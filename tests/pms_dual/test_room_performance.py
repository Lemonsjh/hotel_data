"""JL01 只汇总两侧都有的营业日，比率用相加后的间夜、房费、可售和总房重算。"""
from datetime import date, datetime
from decimal import Decimal
import unittest

from pms_dual.business import JL01_COLUMNS
from pms_dual.room_performance import build_rezen_rows, merge_jl01, meituan_rows, rentable_rooms
from pms_dual.store import TABLES, create_statement
from rezen_pms.room_fee import nights_by_room_type, normalize_lines, revenue_by_room_type


STAMP = datetime(2026, 11, 11, 13, 0, 0)
YESTERDAY = date(2026, 11, 10)
LAST_YEAR = date(2025, 11, 10)


def line(room, account, amount, checkout, name="溪眠", code="100"):
    return {
        "room_type_name": name, "room_type_code": "SSSC", "room": room, "account": account,
        "code": code, "amount": Decimal(amount), "checkout": checkout,
    }


def component(day, name, platform, nights, revenue, total, available, occ="1", adr="1", revpar="1"):
    return {
        "business_date": day, "room_type_name": name, "pms_room_type_id": "A", "platform": platform,
        "room_nights": Decimal(nights), "room_revenue": Decimal(revenue),
        "total_rooms": total, "available_rooms": available,
        "occupancy_rate": Decimal(occ), "adr": Decimal(adr), "revpar": Decimal(revpar),
    }


def payload(day, rows):
    return {"code": 10000, "data": {
        "variables": {"startDate": day.isoformat(), "endDate": day.isoformat(), "orgId": "123", "hotelId": "123"},
        "data": {"summaryList": rows},
    }}


def summary(key, name, rooms, nights, occ, fee):
    return {
        "roomTypeKey": key, "roomTypeName": name, "roomCount": rooms, "roomPoint": nights,
        "rentRate": occ, "avgRoomPrice": "100", "revpar": "45", "roomFee": fee,
        "nightRoomCount": nights, "nightRentRate": occ,
    }


class RoomFeeTests(unittest.TestCase):
    def test_same_room_counts_distinct_positive_amounts_once_per_amount(self):
        day = YESTERDAY
        later = date(2026, 11, 12)
        rows = [
            line("101", "a", "260.78", later),
            line("101", "b", "366.00", later),
            line("102", "c", "280.00", later),
            line("102", "d", "280.00", later),
            line("102", "e", "0", later),
            line("103", "f", "100", later),
            line("103", "f", "-100", later),
            line("104", "g", "80", day),
        ]
        self.assertEqual(nights_by_room_type(rows, day), {"溪眠": Decimal(4)})
        self.assertEqual(revenue_by_room_type(rows)["溪眠"], Decimal("1266.78"))

    def test_normalize_drops_guest_fields_and_rejects_a_blank_checkout_without_echoing_it(self):
        raw = {"businessDate": "2026-11-10", "rmTypeDrpt": "溪眠", "rmTypeCd": "SSSC", "rmNum": "101",
               "acctNum": "9", "trnCd": "100", "trnAmt": "10", "guestName": "秘密客人",
               "checkoutTime": "2026-11-12 12:00:00"}
        parsed = normalize_lines([raw], YESTERDAY)
        self.assertNotIn("guestName", parsed[0])
        self.assertEqual(parsed[0]["checkout"], date(2026, 11, 12))
        raw["checkoutTime"] = None
        raw["guestName"] = "秘密客人"
        with self.assertRaisesRegex(ValueError, "离店日期") as caught:
            normalize_lines([raw], YESTERDAY)
        self.assertNotIn("秘密客人", str(caught.exception))


class RoomPerformanceTests(unittest.TestCase):
    def test_meituan_rate_restores_the_rentable_count_and_last_year_may_be_empty(self):
        self.assertEqual(rentable_rooms("9", "47.37", "20"), 19)
        detail = summary("TYPE-A", "观岚", "20", "9", "47.37%", "1398.12")
        total = summary("总计", "总计", "20", "9", "47.37%", "1398.12")
        rows = meituan_rows(payload(YESTERDAY, [detail, total]), "123", YESTERDAY, "h1", "测试酒店", allow_empty=False)
        self.assertEqual(rows[0]["available_rooms"], 19)
        self.assertEqual(rows[0]["total_rooms"], 20)
        self.assertEqual(rows[0]["occupancy_rate"], Decimal("47.3700"))
        empty = payload(LAST_YEAR, [])
        self.assertEqual(meituan_rows(empty, "123", LAST_YEAR, "h1", "测试酒店", allow_empty=True), [])

    def test_rezen_rows_use_chart_totals_and_must_match_the_hotel_day(self):
        inventory = [
            {"room_type_name": "溪眠", "room_type_code": "SSSC", "total_rooms": 20},
            {"room_type_name": "观岚", "room_type_code": "XZTF", "total_rooms": 10},
        ]
        lines = [line("101", "a", "100.00", date(2026, 11, 12))]
        stats = {"room_count": 30, "nights": Decimal(1), "revenue": Decimal("100.00")}
        rows = build_rezen_rows(lines, inventory, stats, YESTERDAY, "h1", "测试酒店")
        by_name = {row["room_type_name"]: row for row in rows}
        self.assertEqual(by_name["溪眠"]["room_nights"], Decimal("1"))
        self.assertEqual(by_name["溪眠"]["available_rooms"], 20)
        self.assertEqual(by_name["观岚"]["room_nights"], Decimal("0"))
        self.assertEqual(by_name["观岚"]["room_revenue"], Decimal("0.00"))
        stats["room_count"] = 90
        with self.assertRaisesRegex(ValueError, "总房"):
            build_rezen_rows(lines, inventory, stats, YESTERDAY, "h1", "测试酒店")

    def test_merge_recalculates_shared_room_types_and_skips_a_one_sided_day(self):
        left = [
            component(YESTERDAY, "溪眠", "pms_meituan", "9", "900", 20, 19, "47.37", "100", "45"),
            component(YESTERDAY, "观岚", "pms_meituan", "1", "100", 10, 10, "10", "100", "10"),
            component(LAST_YEAR, "溪眠", "pms_meituan", "1", "50", 20, 20, "5", "50", "2.5"),
        ]
        right = [
            component(YESTERDAY, "溪眠", "pms_rezen", "1", "100", 10, 10, "10", "100", "10"),
            component(YESTERDAY, "山语", "pms_rezen", "0", "0", 5, 5, "0", "0", "0"),
        ]
        merged = merge_jl01(left, right, "h1", "测试酒店", STAMP, {("pms_meituan", "溪眠"): "U1"})
        self.assertEqual([list(row) for row in merged], [list(JL01_COLUMNS)] * 3)
        by_name = {row["room_type_name"]: row for row in merged}
        self.assertEqual(set(by_name), {"溪眠", "观岚", "山语"})
        self.assertEqual(by_name["溪眠"]["room_nights"], Decimal("10.0000"))
        self.assertEqual(by_name["溪眠"]["room_revenue"], Decimal("1000.00"))
        self.assertEqual(by_name["溪眠"]["occupancy_rate"], Decimal("34.4828"))
        self.assertEqual(by_name["溪眠"]["adr"], Decimal("100.0000"))
        self.assertEqual(by_name["溪眠"]["revpar"], Decimal("33.3333"))
        self.assertEqual(by_name["溪眠"]["room_type_id"], "U1")
        self.assertEqual(by_name["观岚"]["occupancy_rate"], Decimal("10.0000"))
        self.assertIsNone(by_name["山语"]["room_type_id"])
        self.assertTrue(all(row["business_date"] == YESTERDAY for row in merged))
        self.assertEqual(by_name["溪眠"]["source_platform"], "PMS（美团+Rezen）")
        self.assertEqual(by_name["溪眠"]["pms_rate_room_type_id"], "")

    def test_merge_rejects_a_missing_yesterday_or_conflicting_room_type(self):
        left = [component(YESTERDAY, "溪眠", "pms_meituan", "1", "10", 10, 10)]
        right = [component(LAST_YEAR, "溪眠", "pms_rezen", "1", "10", 10, 10)]
        with self.assertRaisesRegex(ValueError, "昨天"):
            merge_jl01(left, right, "h1", "测试酒店", STAMP, {})
        right = [component(YESTERDAY, "溪眠", "pms_rezen", "1", "10", 10, 10)]
        aliases = {("pms_meituan", "溪眠"): "U1", ("pms_rezen", "溪眠"): "U2"}
        with self.assertRaisesRegex(ValueError, "不同的统一房型"):
            merge_jl01(left, right, "h1", "测试酒店", STAMP, aliases)

    def test_create_statement_covers_jl01_without_altering_columns(self):
        self.assertIn("jl01_room_type_performance_daily", TABLES)
        statement = create_statement("jl01_room_type_performance_daily")
        self.assertIn("CREATE TABLE IF NOT EXISTS `jl01_room_type_performance_daily`", statement)
        self.assertNotIn("ALTER TABLE", statement)
