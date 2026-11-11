"""JL11 留空没有房日的比率，RS01 把同一科目加总后仍能对上房费。"""
from datetime import date, datetime
from decimal import Decimal
import unittest

from pms_dual.business import JL11_COLUMNS, RS01_COLUMNS
from pms_dual.classification import merge_jl11, rezen_room_totals
from pms_dual.store import TABLES, create_statement
from rezen_pms.room_fee import assigned_nights, collapse_revenue, nights_by_room_type, revenue_lines


DAY = date(2026, 11, 10)
NEXT = date(2026, 11, 11)
STAMP = datetime(2026, 11, 11, 14, 0, 0)
START = date(2026, 10, 12)


def raw_line(**overrides):
    row = {
        "businessDate": "2026-11-10", "rmTypeDrpt": "溪眠", "rmTypeCd": "SSSC", "rmNum": "101",
        "acctNum": "9", "trnCd": "100", "trnDrpt1": "房费", "trnAmt": "100.00", "trnId": "T1",
        "guestName": "秘密客人", "checkoutTime": "2026-11-12 12:00:00", "arrivalTime": "2026-11-10 15:00:00",
        "roomRate": "260.00", "rateCd": "OTA", "operNm": "前台",
    }
    row.update(overrides)
    return row


def parsed(**overrides):
    row = raw_line(**overrides)
    return revenue_lines([row], DAY, DAY)[0]


def meituan_type(name, nights, fee, rooms="600", occ="10", overnight=None):
    return {
        "room_type_name": name, "snapshot_date": NEXT, "snapshot_time": STAMP,
        "period_start": START, "period_end": DAY, "section": "summary",
        "room_count": Decimal(rooms), "room_nights": Decimal(nights), "occupancy_rate": Decimal(occ),
        "room_revenue": Decimal(fee), "average_room_price": Decimal("100"), "revpar": Decimal("20"),
        "overnight_room_count": Decimal(overnight if overnight is not None else nights),
        "overnight_occupancy_rate": Decimal(occ),
    }


class RevenueLineTests(unittest.TestCase):
    def test_same_subject_is_summed_and_guest_fields_stay_out(self):
        lines = revenue_lines([
            raw_line(trnAmt="100.00", trnId="T1"),
            raw_line(trnAmt="50.00", trnId="T2"),
            raw_line(trnAmt="-20.00", trnCd="103", trnDrpt1="房费-调整", checkoutTime=None, trnId="T3"),
        ], DAY, DAY)
        self.assertTrue(all("guestName" not in line and "trnId" not in line for line in lines))
        self.assertEqual(sum(assigned_nights(lines, DAY).values(), Decimal(0)),
                         sum(nights_by_room_type(lines, DAY).values(), Decimal(0)))
        rows = collapse_revenue(lines)
        by_subject = {row["charge_subject"]: row for row in rows}
        self.assertEqual(by_subject["房费"]["room_fee"], Decimal("150.00"))
        self.assertEqual(by_subject["房费"]["room_nights"], Decimal(1))
        self.assertEqual(by_subject["房费-调整"]["room_fee"], Decimal("-20.00"))
        self.assertEqual(by_subject["房费-调整"]["room_nights"], Decimal(0))
        self.assertIsNone(by_subject["房费"]["guest_name"])
        self.assertNotIn("秘密客人", str(rows))
        self.assertNotIn("T1", str(rows))

    def test_same_positive_amount_on_one_room_is_one_night(self):
        lines = [
            parsed(acctNum="b", trnAmt="100.00", trnId="T1"),
            parsed(acctNum="a", rmNum="101", trnAmt="100.00", trnId="T2"),
        ]
        rows = {row["order_id"]: row for row in collapse_revenue(lines)}
        self.assertEqual(rows["a"]["room_nights"] + rows["b"]["room_nights"], Decimal(1))
        self.assertEqual(rows["a"]["room_fee"] + rows["b"]["room_fee"], Decimal("200.00"))

    def test_blank_checkout_does_not_echo_the_guest(self):
        with self.assertRaisesRegex(ValueError, "离店日期") as caught:
            revenue_lines([raw_line(checkoutTime=None, guestName="秘密客人")], DAY, DAY)
        self.assertNotIn("秘密客人", str(caught.exception))


class ClassificationTests(unittest.TestCase):
    def test_shared_room_type_keeps_nights_and_fee_and_leaves_rates_empty(self):
        merged = merge_jl11(
            [meituan_type("溪眠", "60", "8000", rooms="600", occ="10"),
             meituan_type("电竞", "2", "400", rooms="220", occ="0.91")],
            {"溪眠": {"room_nights": Decimal(10), "room_revenue": Decimal("1000.00")},
             "山语": {"room_nights": Decimal(3), "room_revenue": Decimal("300.00")}},
            "h1", "测试酒店", STAMP, {})
        self.assertEqual([list(row) for row in merged], [list(JL11_COLUMNS)] * 3)
        by_name = {row["room_type_name"]: row for row in merged}
        self.assertEqual(by_name["溪眠"]["room_nights"], Decimal("70.0000"))
        self.assertEqual(by_name["溪眠"]["room_revenue"], Decimal("9000.00"))
        self.assertEqual(by_name["溪眠"]["average_room_price"], Decimal("128.5714"))
        self.assertIsNone(by_name["溪眠"]["room_count"])
        self.assertIsNone(by_name["溪眠"]["occupancy_rate"])
        self.assertIsNone(by_name["溪眠"]["revpar"])
        self.assertIsNone(by_name["溪眠"]["overnight_room_count"])
        self.assertIsNone(by_name["溪眠"]["overnight_occupancy_rate"])
        self.assertEqual(by_name["电竞"]["room_count"], Decimal("220"))
        self.assertEqual(by_name["电竞"]["occupancy_rate"], Decimal("0.91"))
        self.assertEqual(by_name["电竞"]["overnight_room_count"], Decimal("2"))
        self.assertEqual(by_name["山语"]["room_nights"], Decimal("3.0000"))
        self.assertIsNone(by_name["山语"]["room_count"])
        self.assertEqual(by_name["溪眠"]["source_platform"], "PMS（美团+Rezen）")

    def test_one_short_night_is_allowed_and_a_second_day_is_not(self):
        later = date(2026, 11, 12)
        line = {
            "business": DAY, "room_type_name": "溪眠", "room_type_code": "SSSC", "room": "101",
            "account": "a", "subject": "房费", "code": "100", "amount": Decimal("10.00"), "checkout": later,
        }
        daily = {DAY: {"nights": Decimal(2), "revenue": Decimal("10.00")}}
        totals = rezen_room_totals([line], daily)
        self.assertEqual(totals["溪眠"]["room_nights"], Decimal(1))
        other = {**line, "business": NEXT}
        with self.assertRaisesRegex(ValueError, "间夜"):
            rezen_room_totals([line, other], {**daily, NEXT: {"nights": Decimal(2), "revenue": Decimal("10.00")}})
        with self.assertRaisesRegex(ValueError, "房费"):
            rezen_room_totals([line], {DAY: {"nights": Decimal(1), "revenue": Decimal("9.00")}})

    def test_conflicting_unified_room_type_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "不同的统一房型"):
            merge_jl11([meituan_type("溪眠", "1", "10")],
                       {"溪眠": {"room_nights": Decimal(1), "room_revenue": Decimal("10.00")}},
                       "h1", "测试酒店", STAMP,
                       {("pms_meituan", "溪眠"): "U1", ("pms_rezen", "溪眠"): "U2"})

    def test_create_statements_do_not_alter_columns(self):
        for table, marker in (
            ("jl11_room_type_classification", "jl11_room_type_classification"),
            ("rs01_room_revenue_daily", "uk_room_fee_daily"),
        ):
            self.assertIn(table, TABLES)
            statement = create_statement(table)
            self.assertIn(f"CREATE TABLE IF NOT EXISTS `{table}`", statement)
            self.assertIn(marker, statement)
            self.assertNotIn("ALTER TABLE", statement)
        self.assertIn("charge_subject", RS01_COLUMNS)
        self.assertNotIn("trn_id", RS01_COLUMNS)
