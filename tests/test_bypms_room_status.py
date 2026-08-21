from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "宝寓PMS数据采集代码" / "bypms_room_status_data.py"


def load_module():
    os.environ.setdefault("HOTEL_ID", "test-hotel")
    spec = importlib.util.spec_from_file_location("bypms_room_status_test_target", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load ByPMS room status module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BypmsRoomStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.target = load_module()

    def test_build_rows_keeps_only_rooms_effective_today_without_personal_data(self):
        payload = {
            "state": 0,
            "data": {"data": [
                {"id": 1, "state": "S", "sortState": "Y", "checkIn": "2026-08-18", "checkOut": "2026-08-20", "amount": 2, "roomId": 11, "roomTypeId": 21, "contractId": 31, "channel": "Meituan", "channelChs": "美团酒店", "contractType": "D", "contractCreateTime": "2026-08-17 09:00:00", "infoRealname": "不应保存", "infoPhone": "不应保存", "infoRemark": "不应保存"},
                {"id": 2, "state": "P", "checkIn": "2026-08-20", "checkOut": "2026-08-21", "roomId": 12},
                {"id": 3, "state": "L", "checkIn": "2026-08-19", "checkOut": "2026-08-21", "roomId": 13},
            ]},
        }

        rows = self.target.build_rows(payload, date(2026, 8, 19), datetime(2026, 8, 19, 10, 26, 30))

        self.assertEqual([row["source_record_id"] for row in rows], ["1", "3"])
        self.assertEqual(rows[0]["room_state_name"], "在住")
        self.assertEqual(rows[1]["room_state_name"], "锁房")
        self.assertEqual(rows[0]["snapshot_hour"], datetime(2026, 8, 19, 10))
        self.assertNotIn("infoRealname", rows[0])
        self.assertNotIn("infoPhone", rows[0])
        self.assertNotIn("infoRemark", rows[0])

    def test_active_today_excludes_checkout_date(self):
        self.assertFalse(self.target.active_today({"checkIn": "2026-08-18", "checkOut": "2026-08-19"}, date(2026, 8, 19)))

    def test_state_label_keeps_unknown_code_visible(self):
        self.assertEqual(self.target.state_label("P"), "预订")
        self.assertEqual(self.target.state_label("X"), "未知")

    def test_state_form_uses_posted_month_view_range(self):
        self.assertEqual(
            self.target.state_form(date(2026, 8, 20)),
            {"date": "2026-08-20,2026-09-09", "tagId": "0", "tbl": "monthA"},
        )

    def test_fetch_payload_posts_the_form(self):
        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"state": 0, "data": {"data": []}}

        class Session:
            def __init__(self):
                self.args = None

            def post(self, *args, **kwargs):
                self.args = (args, kwargs)
                return Response()

        session = Session()
        old_cookie = self.target.COOKIE
        self.target.COOKIE = "test-cookie"
        try:
            self.assertEqual(self.target.fetch_payload(date(2026, 8, 20), session), {"state": 0, "data": {"data": []}})
        finally:
            self.target.COOKIE = old_cookie
        args, kwargs = session.args
        self.assertEqual(args, (self.target.STATE_URL,))
        self.assertEqual(kwargs["data"], {"date": "2026-08-20,2026-09-09", "tagId": "0", "tbl": "monthA"})

    def test_extract_room_master_reads_page_inline_data(self):
        master = self.target.extract_room_master(
            '<script>window._ROOMS = {"typeVos":[{"id":21,"name":"大床房"}],'
            '"vos":[{"id":11,"type":21}]};</script>'
        )

        self.assertEqual(master["typeVos"][0]["name"], "大床房")
        self.assertEqual(master["vos"][0]["type"], 21)

    def test_room_type_inventory_uses_room_master_and_page_stock_formula(self):
        payload = {"data": {"data": [
            {"id": 1, "state": "S", "roomId": 11, "checkIn": "2026-08-19", "checkOut": "2026-08-20"},
            {"id": 2, "state": "P", "roomId": 12, "checkIn": "2026-08-20", "checkOut": "2026-08-21"},
            {"id": 3, "state": "L", "roomId": 13, "checkIn": "2026-08-19", "checkOut": "2026-08-21", "tag": "fix"},
            {"id": 4, "state": "L", "roomId": 14, "checkIn": "2026-08-19", "checkOut": "2026-08-21"},
        ]}}
        master = {"typeVos": [{"id": 21, "name": "大床房"}, {"id": 22, "name": "双床房"}], "vos": [
            {"id": 11, "type": 21}, {"id": 12, "type": 21}, {"id": 13, "type": 21}, {"id": 14, "type": 22},
        ]}

        rows = self.target.room_type_inventory_rows(payload, master, date(2026, 8, 20), datetime(2026, 8, 20, 10, 26, 30))
        by_type = {row["bypms_room_type_id"]: row for row in rows}

        self.assertEqual(by_type["21"]["total_rooms"], 3)
        self.assertEqual(by_type["21"]["sold_rooms"], 1)
        self.assertEqual(by_type["21"]["arrival_rooms"], 1)
        self.assertEqual(by_type["21"]["departure_rooms"], 1)
        self.assertEqual(by_type["21"]["repair_rooms"], 1)
        self.assertEqual(by_type["21"]["saleable_rooms"], 2)
        self.assertEqual(by_type["21"]["remaining_saleable_rooms"], 1)
        self.assertEqual(by_type["21"]["occupancy_rate"], 50)
        self.assertEqual(by_type["22"]["locked_rooms"], 1)
        self.assertNotIn("room_id", by_type["21"])


if __name__ == "__main__":
    unittest.main()
