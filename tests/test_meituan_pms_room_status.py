import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "meituan_pms"))
from room_status import parse_room_types, transform


class MeituanPmsRoomStatusTests(unittest.TestCase):
    def setUp(self):
        self.types = [{"id": "A", "roomTypeName": "大床房"}]
        self.data = {"code": 10000, "data": [{"roomTypeName": "大床房", "totalCount": "10",
                      "details": [{"date": "2026-10-27 00:00:00", "availableCount": "4",
                                   "occupationCount": "6", "overbookingCount": "0"}]}]}
        self.now = datetime(2026, 10, 27, 18, 42)

    def rows(self):
        return transform(self.types, self.data, self.now, "hotel", "酒店")

    def test_matches_id_and_current_day_counts(self):
        row = self.rows()[0]
        self.assertEqual((row["pms_room_type_id"], row["total_rooms"], row["available_rooms"],
                          row["occupied_rooms"], row["overbooking_rooms"]), ("A", 10, 4, 6, 0))
        self.assertEqual(row["snapshot_hour"], datetime(2026, 10, 27, 18))

    def test_parses_room_types_and_rejects_duplicate_names(self):
        response = {"code": 10000, "data": [{"roomTypeId": "A", "roomTypeName": "大床房", "availableRoomCount": 10}]}
        self.assertEqual(parse_room_types(response), self.types)
        response["data"].append({"roomTypeId": "B", "roomTypeName": "大床房"})
        with self.assertRaises(ValueError):
            parse_room_types(response)

    def test_skips_only_explicit_aggregation_rows(self):
        self.data["data"].append({"roomTypeName": "总计", "isAggregation": True})
        self.assertEqual(len(self.rows()), 1)
        for flag in (None, False, "true", 1):
            with self.subTest(flag=flag):
                self.data["data"][-1]["isAggregation"] = flag
                with self.assertRaises(ValueError):
                    self.rows()

    def test_aggregation_does_not_mask_missing_or_duplicate_real_room(self):
        import copy
        real = copy.deepcopy(self.data["data"][0])
        summary = {"roomTypeName": "总计", "isAggregation": True}
        for items in ([summary], [real, copy.deepcopy(real), summary]):
            with self.subTest(items=len(items)):
                self.data["data"] = items
                with self.assertRaises(ValueError):
                    self.rows()

    def test_rejects_incomplete_ambiguous_and_invalid_responses(self):
        for mutation in (lambda: self.types.append({"id": "B", "roomTypeName": "大床房"}),
                         lambda: self.data["data"].clear(),
                         lambda: self.data["data"][0]["details"][0].update(availableCount=None),
                         lambda: self.data["data"][0]["details"].append(self.data["data"][0]["details"][0])):
            self.setUp()
            mutation()
            with self.assertRaises(ValueError):
                self.rows()
