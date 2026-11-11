import unittest
from datetime import date, datetime
from decimal import Decimal

from rezen_pms.transform import room_chart_rows, room_status_rows


STAMP = datetime(2026, 11, 10, 10, 15, 0)


def chart():
    available = {"items": [
        {"roomTypeCd": "SSSC", "roomTypeName": "溪眠舒适双床房",
         "items": {"ROOM_DETAIL_AVAILABLE": 1, "ROOM_DETAIL_PHYSICS": 3}},
        {"roomTypeCd": "FSSC", "roomTypeName": "筑风复式双床房",
         "items": {"ROOM_DETAIL_AVAILABLE": 0, "ROOM_DETAIL_PHYSICS": 1}},
    ]}
    rooms = {"roomInfoList": [
        {"rmNum": "101", "rmTyp": "SSSC", "roomStatus": "OC"},
        {"rmNum": "102", "rmTyp": "SSSC", "roomStatus": "OOO"},
        {"rmNum": "103", "rmTyp": "SSSC", "roomStatus": "VC"},
        {"rmNum": "201", "rmTyp": "FSSC", "roomStatus": "OD"},
    ]}
    return available, rooms


def forecast():
    return {"retcode": 0, "body": {"items": [
        {"salesForecastCd": "SUMMARY", "salesForecastName": "合计", "detailRows": [
            {"code": "ROOMNUMS", "name": "房数", "1": 99, "2": 99},
            {"name": "房费", "1": 1, "2": 1}]},
        {"salesForecastCd": "SSSC", "salesForecastName": "溪眠舒适双床房", "detailRows": [
            {"code": "ROOMNUMS", "name": "房数", "1": 2, "2": "1"},
            {"name": "房费", "1": "148.00", "2": "74.5"},
            {"name": "平均房价", "1": "74", "2": "74.5"},
            {"name": "餐费", "1": 0, "2": 0}]},
        {"salesForecastCd": "FSSC", "salesForecastName": "筑风复式双床房", "detailRows": [
            {"name": "房费", "1": "205.4300"}]},
    ]}}


class RezenTransformTests(unittest.TestCase):
    def test_room_chart_counts_occupied_separately_from_repair(self):
        available, rooms = chart()
        rows = room_chart_rows(available, rooms, STAMP, "h1", "测试酒店")
        by_code = {row["pms_room_type_id"]: row for row in rows}
        self.assertEqual(by_code["SSSC"]["occupied_rooms"], 1)
        self.assertEqual(by_code["SSSC"]["total_rooms"], 3)
        self.assertEqual(by_code["SSSC"]["available_rooms"], 1)
        self.assertEqual(by_code["SSSC"]["overbooking_rooms"], 0)
        self.assertEqual(by_code["FSSC"]["occupied_rooms"], 1)
        self.assertEqual(by_code["FSSC"]["available_rooms"], 0)
        self.assertEqual(by_code["FSSC"]["overbooking_rooms"], 0)
        self.assertEqual(rows[0]["source_platform"], "PMS（Rezen）")
        self.assertIsNone(rows[0]["room_revenue"])

    def test_room_chart_rejects_a_room_missing_from_the_type_list(self):
        available, rooms = chart()
        rooms["roomInfoList"].append({"rmNum": "301", "rmTyp": "OTHER", "roomStatus": "O"})
        with self.assertRaisesRegex(ValueError, "不一致"):
            room_chart_rows(available, rooms, STAMP, "h1", "测试酒店")

    def test_overbooking_is_derived_and_a_negative_result_is_rejected(self):
        available, rooms = chart()
        available["items"][0]["items"]["ROOM_DETAIL_PHYSICS"] = 2
        rows = room_chart_rows(available, rooms, STAMP, "h1", "测试酒店")
        self.assertEqual(rows[0]["overbooking_rooms"], 1)
        available["items"][0]["items"]["ROOM_DETAIL_PHYSICS"] = 4
        with self.assertRaisesRegex(ValueError, "超售无效"):
            room_chart_rows(available, rooms, STAMP, "h1", "测试酒店")

    def test_unknown_room_status_is_rejected(self):
        available, rooms = chart()
        rooms["roomInfoList"][0]["roomStatus"] = "XX"
        with self.assertRaisesRegex(ValueError, "无法识别"):
            room_chart_rows(available, rooms, STAMP, "h1", "测试酒店")

    def test_revenue_joins_today_and_rates_are_calculated_from_this_row(self):
        available, rooms = chart()
        rows = room_status_rows(available, rooms, forecast(), STAMP, "h1", "测试酒店")
        by_code = {row["pms_room_type_id"]: row for row in rows}
        self.assertEqual(by_code["SSSC"]["stay_date"], date(2026, 11, 10))
        self.assertEqual(by_code["SSSC"]["occupied_rooms"], 1)
        self.assertEqual(by_code["SSSC"]["room_revenue"], Decimal("148.0000"))
        self.assertEqual(by_code["SSSC"]["adr"], Decimal("148.0000"))
        self.assertEqual(by_code["SSSC"]["revpar"], Decimal("49.3333"))
        self.assertEqual(by_code["FSSC"]["room_revenue"], Decimal("205.4300"))
        self.assertEqual(by_code["FSSC"]["adr"], Decimal("205.4300"))
        self.assertEqual(by_code["FSSC"]["revpar"], Decimal("205.4300"))

    def test_vacant_rooms_beyond_remaining_availability_are_sold(self):
        available = {"items": [{
            "roomTypeCd": "SSSC", "roomTypeName": "溪眠舒适双床房",
            "items": {"ROOM_DETAIL_AVAILABLE": 1, "ROOM_DETAIL_PHYSICS": 5},
        }]}
        rooms = {"roomInfoList": [
            {"rmTyp": "SSSC", "roomStatus": "OD"},
            {"rmTyp": "SSSC", "roomStatus": "OOO"},
            {"rmTyp": "SSSC", "roomStatus": "VC"},
            {"rmTyp": "SSSC", "roomStatus": "VC"},
            {"rmTyp": "SSSC", "roomStatus": "VD"},
        ]}
        chart_rows = room_chart_rows(available, rooms, STAMP, "h1", "测试酒店")
        self.assertEqual(chart_rows[0]["available_rooms"], 1)
        self.assertEqual(chart_rows[0]["occupied_rooms"], 3)
        self.assertEqual(chart_rows[0]["in_house_rooms"], 1)
        self.assertEqual(chart_rows[0]["overbooking_rooms"], 0)

        available["items"][0]["items"]["ROOM_DETAIL_AVAILABLE"] = 4
        oversold = room_chart_rows(available, rooms, STAMP, "h1", "测试酒店")
        self.assertEqual(oversold[0]["available_rooms"], 4)
        self.assertEqual(oversold[0]["occupied_rooms"], 1)
        self.assertEqual(oversold[0]["overbooking_rooms"], 1)

        payload = forecast()
        payload["body"]["items"] = [item for item in payload["body"]["items"]
                                    if item.get("salesForecastCd") in {"SUMMARY", "SSSC"}]
        available["items"][0]["items"]["ROOM_DETAIL_AVAILABLE"] = 1
        rows = room_status_rows(available, rooms, payload, STAMP, "h1", "测试酒店")
        self.assertEqual(rows[0]["occupied_rooms"], 3)
        self.assertEqual(rows[0]["adr"], Decimal("148.0000"))

    def test_room_fee_comes_from_the_stay_date(self):
        available, rooms = chart()
        payload = forecast()
        for item in payload["body"]["items"]:
            for row in item["detailRows"]:
                if row.get("name") == "房费":
                    row["data"] = {"2026-11-10": "10.5", "2026-11-11": "99"}
        rows = room_status_rows(available, rooms, payload, STAMP, "h1", "测试酒店")
        self.assertEqual(rows[0]["room_revenue"], Decimal("10.5000"))

    def test_zero_inventory_type_without_rooms_is_omitted(self):
        available, rooms = chart()
        available["items"].append({
            "roomTypeCd": "EMPTY", "roomTypeName": "无房",
            "items": {"ROOM_DETAIL_AVAILABLE": 0, "ROOM_DETAIL_PHYSICS": 0}})
        rows = room_chart_rows(available, rooms, STAMP, "h1", "测试酒店")
        self.assertEqual({row["pms_room_type_id"] for row in rows}, {"SSSC", "FSSC"})

    def test_missing_room_revenue_rejects_the_batch(self):
        available, rooms = chart()
        payload = forecast()
        del payload["body"]["items"][2]
        with self.assertRaisesRegex(ValueError, "不一致"):
            room_status_rows(available, rooms, payload, STAMP, "h1", "测试酒店")
